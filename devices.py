import logging
import serial
import time

from config import *

if IRON_CURTAIN_ENABLED:
    from curtain import BeatBlaster

logger = logging.getLogger(__name__)

# List of all can devices
CAN_DEVICES = dict(reduce(lambda acc, val: acc + val.items(), CAN_DEVICE_GROUPS.values(), []))

# Multicast
CAN_DEVICES[CAN_ALL_ADDRESS] = "(All)"

class DeviceManager(object):
    def __init__(self, devices):
        self.devices = devices
        self.last_tick = (0, 0)

    def tick(self, tick):
        if tick == self.last_tick:
            return None
        beat, fractick = tick
        if tick[0] != self.last_tick[0]:
            fractick = 0

        tick = (beat, fractick)
        if SEND_BEATS and (fractick % FRACTICK_FRAC) == 0:
            for dev in self.devices:
                dev.tick(tick)
        
        self.last_tick = tick

    def reset(self):
        for dev in self.devices:
            self.dev.reset()


class SingleBespeckleDevice(object):
    """
    Abstraction for sending data to a single Bespeckle-based device
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
        self.bespeckle_ids = set()

    def raw_packet(self, data):
        logger.debug("Serial Data: %s", ';'.join(map(lambda x: "{:02x}".format(x), data)))
        self.ser.write("".join([chr(d) for d in data]))
        time.sleep(0.0001) #XXX

    def cobs_packet(self, data):
        rdata = []
        i = 0
        for d in data[::-1]:
            i += 1
            if d == 0:
                rdata.append(i)
                i = 0
            else:
                rdata.append(d)
        self.raw_packet([0, i] + rdata[::-1])
        

    def framed_packet(self, data=None, flags=0x00, addr=0x00):
        if data is None or len(data) > 250:
            raise Exception("invalid data")
        while len(data) < 8:
            data.append(0)
        crc_frame = [flags, addr] + data
        checksum = sum(crc_frame) & 0xff
        frame = [len(data), checksum] + crc_frame
        self.cobs_packet(frame)

    def _get_next_id(self):
        for i in range(256):
            if i not in self.bespeckle_ids:
                return i
        return 0xff # Just overwrite the last effect. lololol

    def tick(self, time):
        beat, frac = time
        self.framed_packet([self.CMD_TICK, frac])
   
    def reset(self):
        self.framed_packet([self.CMD_RESET])
        #TODO: Send Calibration
        #for i, gc in enumerate(CAN_DEVICE_CALIBRATION.get(uid, GLOBAL_CALIBRATION)):
        #    self.canbus.send_to_all([self.canbus.CMD_PARAM, i, int(255.0 * gc) ,0,0, 0,0,0])
        self.bespeckle_ids = set()

    def bespeckle_add_effect(self, bespeckle_class, data=None):
        if data is None:
            data = []
        bespeckle_id = self._get_next_id()
        self.bespeckle_ids.add(bespeckle_id)
        self.framed_packet([bespeckle_class, bespeckle_id] + data)
        return bespeckle_id

    def bespeckle_pop_effect(self, bespeckle_id):
        if bespeckle_id in self.bespeckle_ids:
            self.bespeckle_ids.discard(bespeckle_id)
        self.framed_packet([self.CMD_STOP, bespeckle_id])
        return True

    def bespeckle_msg_effect(self, bespeckle_id, data=None):
        if data is None:
            data = []
        self.framed_packet([self.CMD_MSG, bespeckle_id] + data)
        return bespeckle_id

class FakeSingleBespeckleDevice(SingleBespeckleDevice):
    def __init__(self, *args, **kwargs):
        self.addresses = {}
        self.bespeckle_ids = set()

    def raw_packet(self, data):
        logger.debug("Data: %s", ';'.join(map(lambda x: "{:02x}".format(x), data)))
        time.sleep(0.001)

