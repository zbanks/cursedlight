# Group invididual can devices to get the same set of commands
CAN_DEVICE_GROUPS = {
    "Left Dig": {
        0x0010: "D left0",
        0x0011: "D left1",
        0x0012: "D left2",
    },
    "Top Dig": {
        0x0020: "D top0",
        0x0021: "D top1",
    },
    "Right Dig": {
        0x0030: "D right0",
        0x0031: "D right1",
        0x0032: "D right2",
    },
}

# Multicast
CAN_ALL_ADDRESS = 0x0000

IRON_CURTAIN_ENABLED = True
IRON_CURTAIN = "Iron Curtain"
IRON_CURTAIN_ADDR = "tcp://*:8000"
IRON_CURTAIN_SCENES = [
    'scene 0',
    'scene 1',
    'scene 2',
    'scene 3',
]

KEYBOARD_MAP = {
    "MASTER": 0,
    IRON_CURTAIN: 0,
    "Left Dig": 0,
    "Top Dig": 1,
    "Right Dig": 0,
}
