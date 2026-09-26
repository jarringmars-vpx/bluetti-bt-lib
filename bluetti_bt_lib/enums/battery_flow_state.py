from enum import Enum


class BatteryFlowState(Enum):
    IDLE = 0
    CHARGING = 1
    DISCHARGING = 2
