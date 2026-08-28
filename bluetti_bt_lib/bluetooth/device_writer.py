import asyncio
import logging
from typing import Any

import async_timeout
from bleak import BleakClient
from bleak.exc import BleakError

from .encryption import BluettiEncryption, Message, MessageType, AES_BLOCK_SIZE
from ..const import NOTIFY_UUID, WRITE_UUID
from ..base_devices import BluettiDevice
from ..utils.privacy import mac_loggable


class DeviceWriterConfig:
    def __init__(self, timeout: int = 15, use_encryption: bool = False):
        self.timeout = timeout
        self.use_encryption = use_encryption


class DeviceWriter:
    def __init__(
        self,
        bleak_client: BleakClient,
        bluetti_device: BluettiDevice,
        config: DeviceWriterConfig = DeviceWriterConfig(),
        lock: asyncio.Lock = asyncio.Lock(),
    ):
        self.client = bleak_client
        self.bluetti_device = bluetti_device
        self.config = config
        self.polling_lock = lock

        self.logger = logging.getLogger(
            f"{__name__}.{mac_loggable(bleak_client.address).replace(':', '_')}"
        )

        self.encryption = BluettiEncryption()
        self.encrypted_buffer = bytearray()
        self.encryption_ready = asyncio.Event()

    async def write(self, field: str, value: Any):
        available_fields = [f.name for f in self.bluetti_device.fields]

        if field not in available_fields:
            self.logger.error("Field not supported")
            return

        command = self.bluetti_device.build_write_command(field, value)

        if command is None:
            self.logger.error("Field is not writeable")
            return

        self.logger.debug("Writing to device register")

        async with self.polling_lock:
            try:
                async with async_timeout.timeout(self.config.timeout):

                    if not self.client.is_connected:
                        self.logger.debug("Connecting to device")
                        await self.client.connect()

                    self.logger.debug("Connected to device")

                    if self.config.use_encryption:
                        await self._encrypted_write(bytes(command))
                    else:
                        self.logger.debug("Writing command: %s", command)

                        await self.client.write_gatt_char(
                            WRITE_UUID,
                            bytes(command),
                        )

                    self.logger.debug("Write successful")

            except TimeoutError:
                self.logger.warning("Timeout")
                return None

            except BleakError as err:
                self.logger.warning("Bleak error: %s", err)
                return None

            except BaseException:
                self.logger.exception("Unknown error")
                return None

            finally:
                if self.config.use_encryption:
                    try:
                        await self.client.stop_notify(NOTIFY_UUID)
                    except Exception:
                        pass

                self.encryption.reset()
                self.encrypted_buffer.clear()

                if self.client:
                    await self.client.disconnect()

                self.logger.debug("Disconnected from device")

    async def _encrypted_write(self, command: bytes):
        """Perform the Bluetti encryption handshake and send a command."""

        self.encryption_ready.clear()
        self.encrypted_buffer.clear()

        await self.client.start_notify(
            NOTIFY_UUID,
            self._notification_handler,
        )

        self.logger.debug("Encryption notification handler started")

        try:
            await asyncio.wait_for(
                self.encryption_ready.wait(),
                timeout=self.config.timeout,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                "Timed out waiting for encryption handshake"
            )

        if not self.encryption.is_ready_for_commands:
            raise RuntimeError(
                "Encryption handshake did not produce a secure key"
            )

        self.logger.debug("Encryption handshake complete")

        encrypted_command = self.encryption.aes_encrypt(
            command,
            self.encryption.secure_aes_key,
            None,
        )

        self.logger.debug(
            "Writing encrypted command: %s",
            encrypted_command.hex(),
        )

        await self.client.write_gatt_char(
            WRITE_UUID,
            encrypted_command,
        )

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
        """Handle Bluetti encryption handshake notifications."""

        self.logger.debug(
            "Got notification (%d bytes)",
            len(data),
        )

        message = Message(data)

        # Initial challenge messages are not encrypted.
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

        # Everything after the challenge is encrypted.
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
            self.encrypted_buffer = self.encrypted_buffer[
                expected_len:
            ]
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
                peer_pubkey_response = (
                    self.encryption.msg_peer_pubkey(
                        decrypted
                    )
                )

                if peer_pubkey_response is not None:
                    await self.client.write_gatt_char(
                        WRITE_UUID,
                        peer_pubkey_response,
                    )

                return

            if decrypted.type == MessageType.PUBKEY_ACCEPTED:
                self.encryption.msg_key_accepted(
                    decrypted
                )

                self.logger.debug(
                    "Secure encryption key established"
                )

                self.encryption_ready.set()

                return
