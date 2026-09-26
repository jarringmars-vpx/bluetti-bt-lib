from enum import Enum, unique


@unique
class HAACOutputState(Enum):
    OFF = 32
    ON = 34
