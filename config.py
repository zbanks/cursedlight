# Group invididual can devices to get the same set of commands
CAN_DEVICE_GROUPS = {
    
    "Debug 0":{
        0x0002: "Debugging sucks"
    },
    "Debug 1":{
        0x0001: "Debugging sucks 1"
    },
#    "Left Dig": {
#        0x0000: "D left0",
#        0x0011: "D left1",
#        0x0012: "D left2",
#    },
#    "Top Dig": {
#        0x0000: "D top0",
#        0x0021: "D top1",
#    },
#    "Right Dig": {
#        0x0030: "D right0",
#        0x0031: "D right1",
#        0x0032: "D right2",
#    },
}

FRACTICK_FRAC = 1
SEND_BEATS = True

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
    "Debug": 0,
    "Debug 0": 0,
    "Debug 1": 1,
}
