import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Callable, List, cast

import async_timeout
from bleak import BleakClient, BleakScanner
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .encryption import BluettiEncryption, Message, MessageType, AES_BLOCK_SIZE
from ..base_devices import BluettiDevice
from ..const import NOTIFY_UUID, WRITE_UUID
from ..registers import ReadableRegisters, DeviceRegister
from ..utils.privacy import mac_loggable


class DeviceSessionConfig:
    """Configuration for a persistent BLUETTI BLE session."""

    def __init__(
        self,
        timeout: int = 60,
        use_encryption: bool = False,
        command_timeout: float = 5.0,
        command_retries: int = 1,
        retry_delay: float = 0.4,
    ):
        self.timeout = timeout
        self.use_encryption = use_encryption
        self.command_timeout = command_timeout
        self.command_retries = max(0, int(command_retries))
        self.retry_delay = max(0.0, float(retry_delay))


class DeviceSession:
    """
    Persistent BLUETTI BLE session.

    A DeviceSession owns one BLE connection, one notification subscription,
    one encryption context, and one command lock. Reads and writes therefore
    use the same authenticated BLE session until disconnect() is called.
    """

    def __init__(
        self,
        mac: str,
        bluetti_device: BluettiDevice,
        future_builder_method: Callable[[], asyncio.Future[Any]],
        config: DeviceSessionConfig | None = None,
        lock: asyncio.Lock | None = None,
        ble_client: BleakClient | None = None,
    ):
        self.mac = mac
        self.bluetti_device = bluetti_device
        self.create_future = future_builder_method
        self.config = config or DeviceSessionConfig()
        self.command_lock = lock or asyncio.Lock()

        self.ble_client = ble_client
        """Optional pre-created client, primarily useful for tests."""

        self.logger = logging.getLogger(
            f"{__name__}.{mac_loggable(mac).replace(':', '_')}"
        )

        self.device = None
        self.client: BleakClient | None = None

        self.has_notifier = False
        self.current_registers: DeviceRegister | None = None
        self.notify_response = bytearray()
        self.notify_future: asyncio.Future[Any] | None = None

        self.encryption = BluettiEncryption()
        self.encrypted_buffer = bytearray()
        self.encryption_ready = asyncio.Event()

        # Per-command diagnostics are intentionally buffered instead of
        # printed here so the GUI can keep one logical scan on one console
        # line. Entries are consumed after each public read_registers() call.
        self._scan_diagnostics: list[str] = []

        # Request/response correlation diagnostics. These do not change the
        # protocol behavior; they only preserve enough context to identify
        # which exact Modbus request was active when an unexpected response
        # or timeout occurred.
        self._request_sequence = 0
        self._active_request_id: int | None = None
        self._active_request_plain = b""
        self._active_request_description = ""
        self._active_request_sent_monotonic: float | None = None
        self._last_response_request_id: int | None = None
        self._last_response_timestamp = ""
        self._last_response_delay: float | None = None
        self._orphan_diagnostics: list[str] = []

    @property
    def is_connected(self) -> bool:
        return bool(self.client and self.client.is_connected)

    @property
    def is_ready(self) -> bool:
        if not self.is_connected:
            return False
        if not self.config.use_encryption:
            return True
        return self.encryption.is_ready_for_commands

    async def connect(self) -> None:
        """
        Establish the BLE connection and notification subscription.

        For encrypted devices, wait for the BLUETTI encryption handshake to
        complete. Repeated calls are harmless while already connected/ready.
        """
        if self.is_ready:
            return

        async with self.command_lock:
            if self.is_ready:
                return

            async with async_timeout.timeout(self.config.timeout):
                if self.ble_client:
                    self.device = None
                    self.client = self.ble_client

                    if not self.client.is_connected:
                        await self.client.connect()
                else:
                    self.logger.debug("Searching for device")

                    self.device = await BleakScanner.find_device_by_address(
                        self.mac,
                        timeout=5,
                    )

                    if self.device is None:
                        raise RuntimeError("Device not found")

                    self.logger.debug("Connecting to device")

                    self.client = await establish_connection(
                        BleakClientWithServiceCache,
                        self.device,
                        self.device.name or "Unknown Device",
                        max_attempts=10,
                    )

                self.logger.debug("Connected to device")

                if not self.has_notifier:
                    await self.client.start_notify(
                        NOTIFY_UUID,
                        self._notification_handler,
                    )
                    self.has_notifier = True
                    self.logger.debug("Notification handler setup complete")

                if self.config.use_encryption:
                    if self.encryption.is_ready_for_commands:
                        self.encryption_ready.set()
                    else:
                        self.encryption_ready.clear()
                        self.logger.debug(
                            "Waiting for encryption handshake to complete"
                        )

                        await asyncio.wait_for(
                            self.encryption_ready.wait(),
                            timeout=self.config.timeout,
                        )

                    if not self.encryption.is_ready_for_commands:
                        raise RuntimeError(
                            "Encryption handshake did not produce a secure key"
                        )

                    self.logger.debug("Encryption handshake complete")

    async def disconnect(self) -> None:
        """Stop notifications, disconnect, and reset session state."""
        async with self.command_lock:
            if self.has_notifier and self.client:
                try:
                    await self.client.stop_notify(NOTIFY_UUID)
                    self.logger.debug("Stopped notifier")
                except Exception:
                    pass

                self.has_notifier = False

            if self.client:
                try:
                    if self.client.is_connected:
                        await self.client.disconnect()
                        self.logger.debug("Disconnected from device")
                finally:
                    self.client = None

            self.notify_future = None
            self.notify_response.clear()
            self.current_registers = None

            self.encryption.reset()
            self.encrypted_buffer.clear()
            self.encryption_ready.clear()
            self._scan_diagnostics.clear()
            self._orphan_diagnostics.clear()
            self._active_request_id = None
            self._active_request_plain = b""
            self._active_request_description = ""
            self._active_request_sent_monotonic = None
            self._last_response_request_id = None
            self._last_response_timestamp = ""
            self._last_response_delay = None

    async def read(
        self,
        only_registers: List[ReadableRegisters] | None = None,
        raw: bool = False,
    ) -> dict | None:
        """
        Read registers over the existing persistent BLE connection.

        connect() must have completed successfully first. This method does not
        stop notifications, disconnect, or reset encryption after the read.
        """
        if not self.is_ready:
            raise RuntimeError(
                "DeviceSession is not connected and ready. Call connect() first."
            )

        registers = self.bluetti_device.get_polling_registers()
        pack_registers = self.bluetti_device.get_pack_polling_registers()

        if only_registers is not None:
            registers = only_registers
            pack_registers = []

        parsed_data: dict = {}

        async with self.command_lock:
            async with async_timeout.timeout(self.config.timeout):
                for register in registers:
                    body = register.parse_response(
                        await self._async_send_command(register)
                    )

                    self.logger.debug("Raw data: %s", body)

                    if raw:
                        parsed_data[register.starting_address] = body
                        continue

                    parsed = self.bluetti_device.parse(
                        register.starting_address,
                        body,
                    )

                    self.logger.debug("Parsed data: %s", parsed)
                    parsed_data.update(parsed)

                for pack in range(1, self.bluetti_device.max_packs + 1):
                    selector = self.bluetti_device.get_pack_selector(pack)

                    selector_body = selector.parse_response(
                        await self._async_send_command(selector)
                    )

                    self.logger.debug(
                        "Pack selector response for pack %d: %s",
                        pack,
                        selector_body,
                    )

                    await asyncio.sleep(3)

                    for register in pack_registers:
                        body = register.parse_response(
                            await self._async_send_command(register)
                        )

                        self.logger.debug("Raw data: %s", body)

                        if raw:
                            parsed_data[register.starting_address] = body
                            continue

                        parsed = self.bluetti_device.parse(
                            register.starting_address,
                            body,
                            pack_num=pack,
                        )

                        self.logger.debug("Parsed data: %s", parsed)
                        parsed_data.update(parsed)

        return parsed_data or None

    def consume_scan_diagnostics(self) -> list[str]:
        """
        Return and clear diagnostics collected by the most recent targeted read.

        This keeps lower-level timeout/malformed-response evidence available
        to callers without forcing DeviceSession to print extra console lines.
        """
        diagnostics = list(self._orphan_diagnostics) + list(self._scan_diagnostics)
        self._orphan_diagnostics.clear()
        self._scan_diagnostics.clear()
        return diagnostics

    async def read_registers(
        self,
        starting_address: int,
        count: int,
    ) -> dict[int, int]:
        """
        Read one contiguous holding-register block over the persistent session.

        Returns a mapping of absolute register address to unsigned 16-bit value.

        This is intentionally a low-level public API. It does not require the
        requested registers to have named device fields, which makes it useful
        for efficient demand-driven polling of known-good blocks.

        A short/malformed response is retried on the SAME authenticated BLE
        session. It is not treated as a connection failure by itself.
        """
        if not self.is_ready:
            raise RuntimeError(
                "DeviceSession is not connected and ready. Call connect() first."
            )

        if not isinstance(starting_address, int):
            raise TypeError("starting_address must be an integer")
        if not isinstance(count, int):
            raise TypeError("count must be an integer")
        if starting_address < 0 or starting_address > 0xFFFF:
            raise ValueError("starting_address must be between 0 and 65535")
        if count < 1 or count > 125:
            raise ValueError("count must be between 1 and 125")
        if starting_address + count - 1 > 0xFFFF:
            raise ValueError("requested register block exceeds address 65535")

        registers = ReadableRegisters(starting_address, count)
        attempts = self.config.command_retries + 1
        last_error: Exception | None = None

        # One public targeted scan owns one diagnostics bundle. _async_send_command
        # may append timeout information; this method may append parse/length data.
        self._scan_diagnostics.clear()

        async with self.command_lock:
            async with async_timeout.timeout(self.config.timeout):
                for attempt in range(attempts):
                    try:
                        response = await self._async_send_command(registers)
                        body = registers.parse_response(response)

                        expected_bytes = count * 2
                        if len(body) != expected_bytes:
                            response_hex = bytes(response).hex(" ").upper()
                            body_hex = bytes(body).hex(" ").upper()
                            request_hex = self._active_request_plain.hex(" ").upper()
                            response_delay = (
                                f"{self._last_response_delay:.3f}s"
                                if self._last_response_delay is not None
                                else "unknown"
                            )
                            self._scan_diagnostics.append(
                                "UNEXPECTED "
                                f"request_id={self._last_response_request_id} "
                                f"request={self._active_request_description} "
                                f"TXHex={request_hex} "
                                f"RXat={self._last_response_timestamp or 'unknown'} "
                                f"rx_after={response_delay} "
                                f"expected={expected_bytes}B "
                                f"actual={len(body)}B "
                                f"ResponseHex={response_hex} "
                                f"DataHex={body_hex}"
                            )
                            raise ValueError(
                                f"Expected {expected_bytes} data bytes for "
                                f"{count} registers, got {len(body)}"
                            )

                        result: dict[int, int] = {}
                        for offset in range(count):
                            index = offset * 2
                            value = int.from_bytes(
                                body[index:index + 2],
                                "big",
                                signed=False,
                            )
                            result[starting_address + offset] = value

                        return result

                    except ValueError as err:
                        last_error = err

                        if not self.is_ready:
                            raise RuntimeError(
                                "BLE session became unavailable while reading "
                                f"R{starting_address}-"
                                f"R{starting_address + count - 1}"
                            ) from err

                        if attempt >= attempts - 1:
                            raise

                        self._scan_diagnostics.append(
                            f"RETRY reason=malformed delay={self.config.retry_delay:.3f}s"
                        )
                        self.logger.debug(
                            "Malformed/short response reading R%d-R%d; "
                            "retrying on same session after %.3fs: %s",
                            starting_address,
                            starting_address + count - 1,
                            self.config.retry_delay,
                            err,
                        )

                        self.notify_future = None
                        self.notify_response.clear()
                        self.encrypted_buffer.clear()

                        if self.config.retry_delay:
                            await asyncio.sleep(self.config.retry_delay)

        if last_error is not None:
            raise last_error

        raise RuntimeError("Targeted register read ended unexpectedly")

    async def write(self, field: str, value: Any) -> bool:
        """
        Write a device field over the existing persistent BLE connection.

        The write uses the same BleakClient, notification subscription, and
        encryption context as read(). This method does not reconnect,
        re-authenticate, stop notifications, or disconnect.

        The BLUETTI write protocol used by the existing DeviceWriter does not
        currently parse a write acknowledgement, so callers that require
        confirmation should perform a read-back after this method returns.
        """
        if not self.is_ready:
            raise RuntimeError(
                "DeviceSession is not connected and ready. Call connect() first."
            )

        available_fields = [f.name for f in self.bluetti_device.fields]

        if field not in available_fields:
            raise ValueError(f"Field not supported: {field}")

        command = self.bluetti_device.build_write_command(field, value)

        if command is None:
            raise ValueError(f"Field is not writeable: {field}")

        async with self.command_lock:
            async with async_timeout.timeout(self.config.timeout):
                command_bytes = bytes(command)

                if self.config.use_encryption:
                    if not self.encryption.is_ready_for_commands:
                        raise RuntimeError("Encrypted session is not ready")

                    command_bytes = self.encryption.aes_encrypt(
                        command_bytes,
                        self.encryption.secure_aes_key,
                        None,
                    )

                self.notify_future = None
                self.notify_response.clear()
                self.encrypted_buffer.clear()

                self.logger.debug(
                    "Writing field %s=%r over persistent session",
                    field,
                    value,
                )

                await self.client.write_gatt_char(
                    WRITE_UUID,
                    command_bytes,
                )

                self.logger.debug("Persistent write sent successfully")

        return True

    async def _async_send_command(self, registers: DeviceRegister) -> bytes:
        """
        Send one request/response command over the current session.

        Transient command timeouts are retried on the same BLE/encryption
        session while the session remains connected and authenticated.

        v0.6 also records the exact plaintext Modbus TX request and the timing
        of the response that satisfied the current future. This is diagnostic
        only and intentionally does not alter retry/synchronization behavior.
        """
        attempts = self.config.command_retries + 1

        for attempt in range(attempts):
            if not self.client or not self.client.is_connected:
                raise RuntimeError("BLE session is not connected")

            if self.config.use_encryption:
                if not self.encryption.is_ready_for_commands:
                    raise RuntimeError("Encrypted session is not ready")

            self.current_registers = registers
            self.notify_response = bytearray()
            self.notify_future = self.create_future()
            self.encrypted_buffer.clear()

            plain_command = bytes(registers)
            self._request_sequence += 1
            self._active_request_id = self._request_sequence
            self._active_request_plain = plain_command
            self._active_request_description = str(registers)
            self._active_request_sent_monotonic = time.monotonic()
            self._last_response_request_id = None
            self._last_response_timestamp = ""
            self._last_response_delay = None

            command_bytes = plain_command
            if self.config.use_encryption:
                command_bytes = self.encryption.aes_encrypt(
                    command_bytes,
                    self.encryption.secure_aes_key,
                    None,
                )

            await self.client.write_gatt_char(
                WRITE_UUID,
                command_bytes,
            )

            self.logger.debug(
                "Request sent (%s), request_id=%d, attempt %d/%d, TX=%s",
                registers,
                self._active_request_id,
                attempt + 1,
                attempts,
                plain_command.hex(" ").upper(),
            )

            try:
                response = await asyncio.wait_for(
                    self.notify_future,
                    timeout=self.config.command_timeout,
                )
            except asyncio.TimeoutError:
                request_id = self._active_request_id
                request_hex = self._active_request_plain.hex(" ").upper()
                request_description = self._active_request_description

                self.notify_future = None
                self.notify_response.clear()
                self.encrypted_buffer.clear()

                connected = bool(self.client and self.client.is_connected)
                ready = self.is_ready

                if not connected or not ready:
                    raise RuntimeError(
                        "BLE/encryption session became unavailable during "
                        f"request: {registers}"
                    )

                if attempt >= attempts - 1:
                    raise

                self._scan_diagnostics.append(
                    "TIMEOUT "
                    f"request_id={request_id} "
                    f"request={request_description} "
                    f"TXHex={request_hex} "
                    f"waited={self.config.command_timeout:.3f}s "
                    f"retry_delay={self.config.retry_delay:.3f}s "
                    f"attempt={attempt + 1}/{attempts}"
                )
                self.logger.debug(
                    "Timed out waiting for %s; retrying on same session "
                    "after %.3fs",
                    registers,
                    self.config.retry_delay,
                )

                if self.config.retry_delay:
                    await asyncio.sleep(self.config.retry_delay)

                continue

            self.logger.debug("Got response")
            return cast(bytes, response)

        raise RuntimeError("Command retry loop ended unexpectedly")

    def _calculate_expected_encrypted_length(
        self,
        buffer: bytearray,
    ) -> int | None:
        """Calculate expected total length of an encrypted message."""
        if len(buffer) < 2:
            return None

        data_len = (buffer[0] << 8) + buffer[1]

        key, iv = self.encryption.getKeyIv()

        if iv is None:
            header_size = 6
        else:
            header_size = 2

        padded_len = (
            (data_len + AES_BLOCK_SIZE - 1)
            // AES_BLOCK_SIZE
        ) * AES_BLOCK_SIZE

        return header_size + padded_len

    async def _notification_handler(
        self,
        _: int,
        data: bytearray,
    ):
        """Handle handshake and command-response notifications."""
        self.logger.debug("Got new data (%d bytes)", len(data))

        if self.config.use_encryption:
            message = Message(data)

            if message.is_pre_key_exchange:
                message.verify_checksum()

                if message.type == MessageType.CHALLENGE:
                    challenge_response = self.encryption.msg_challenge(
                        message
                    )

                    if challenge_response is not None:
                        await self.client.write_gatt_char(
                            WRITE_UUID,
                            challenge_response,
                        )

                    return

                if message.type == MessageType.CHALLENGE_ACCEPTED:
                    self.logger.debug("Challenge accepted")
                    return

                return

            if self.encryption.unsecure_aes_key is None:
                self.logger.error(
                    "Received encrypted message before key initialization"
                )
                return

            self.encrypted_buffer.extend(data)

            expected_len = self._calculate_expected_encrypted_length(
                self.encrypted_buffer
            )

            if expected_len is None:
                return

            if len(self.encrypted_buffer) < expected_len:
                self.logger.debug(
                    "Buffering fragment: %d/%d bytes",
                    len(self.encrypted_buffer),
                    expected_len,
                )
                return

            complete_message = bytes(
                self.encrypted_buffer[:expected_len]
            )

            if len(self.encrypted_buffer) > expected_len:
                self.encrypted_buffer = self.encrypted_buffer[expected_len:]
            else:
                self.encrypted_buffer.clear()

            key, iv = self.encryption.getKeyIv()

            try:
                decrypted = Message(
                    self.encryption.aes_decrypt(
                        complete_message,
                        key,
                        iv,
                    )
                )
            except ValueError as err:
                self.logger.error(
                    "Decryption failed: %s",
                    err,
                )
                self.encrypted_buffer.clear()
                return

            if decrypted.is_pre_key_exchange:
                decrypted.verify_checksum()

                if decrypted.type == MessageType.PEER_PUBKEY:
                    peer_pubkey_response = self.encryption.msg_peer_pubkey(
                        decrypted
                    )

                    if peer_pubkey_response is not None:
                        await self.client.write_gatt_char(
                            WRITE_UUID,
                            peer_pubkey_response,
                        )

                    return

                if decrypted.type == MessageType.PUBKEY_ACCEPTED:
                    self.encryption.msg_key_accepted(decrypted)
                    self.encryption_ready.set()

                    self.logger.debug(
                        "Secure encryption key established"
                    )

                    return

            data = decrypted.buffer

        self.notify_response.extend(data)

        rx_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        rx_delay = None
        if self._active_request_sent_monotonic is not None:
            rx_delay = time.monotonic() - self._active_request_sent_monotonic

        if self.notify_future is None:
            # A complete decrypted Modbus-like payload arrived while no
            # command future was active. Preserve it for the next caller so
            # we can determine whether the device emits true unsolicited data.
            orphan_hex = bytes(self.notify_response).hex(" ").upper()
            delay_text = f"{rx_delay:.3f}s" if rx_delay is not None else "unknown"
            self._orphan_diagnostics.append(
                "ORPHAN_RX "
                f"RXat={rx_timestamp} "
                f"after_last_tx={delay_text} "
                f"len={len(self.notify_response)}B "
                f"ResponseHex={orphan_hex}"
            )
            self.notify_response.clear()
            return

        if not self.notify_future.done():
            self._last_response_request_id = self._active_request_id
            self._last_response_timestamp = rx_timestamp
            self._last_response_delay = rx_delay
            self.notify_future.set_result(
                bytes(self.notify_response)
            )
