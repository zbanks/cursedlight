import logging
import serial


from config import *

if IRON_CURTAIN_ENABLED:
    from curtain import BeatBlaster

logger = logging.getLogger(__name__)

# List of all can devices
CAN_DEVICES = dict(reduce(lambda acc, val: acc + val.items(), CAN_DEVICE_GROUPS.values(), []))

# Multicast
CAN_DEVICES[CAN_ALL_ADDRESS] = "(All)"

class CanBus(object):
    """
    Abstraction for sending data at the hardware level.
    """
    CMD_TICK = 0x80
    CMD_RESET = 0x83
    CMD_REBOOT = 0x83
    CMD_MSG = 0x81
    CMD_STOP = 0x82
    CMD_PARAM = 0x85

    def __init__(self, port, baudrate=115200):
        self.ser = serial.Serial(port, baudrate)
        self.addresses = {}

    def raw_packet(self, data):
        logger.debug("CAN data: %s", ';'.join(map(lambda x: "{:02x}".format(x), data)))
        self.ser.write("".join([chr(d) for d in data]))
        time.sleep(0.0001)

    def can_packet(self, addr, can_data):
        if addr not in self.addresses:
            self.addresses[addr] = "(Unknown)"
#can_data = (can_data + [0] * 8)[:8]
        # Format is: [ADDR_H, ADDR_L, LEN, (data), 0xFF]
        data = [addr & 0xff, (addr >> 8) & 0xff, len(can_data)] + can_data + [0xff]
        self.raw_packet(data)

    def send_to_all(self, can_data):
        self.can_packet(CAN_ALL_ADDRESS, can_data)

class FakeCanBus(CanBus):
    """
    ...and sometimes there isn't a hardware level.
    """
    def __init__(self, port, baudrate=115200):
        self.addresses = {}
        logger.debug("Setup fake can bus: %s @ %d baud", port, baudrate)

    def raw_packet(self, data):
        logger.debug("CAN data: %s", ':'.join(map(lambda x: "{:02x}".format(x), data)))
