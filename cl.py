import asyncore
import curses
import evdev
import logging
import serial

logger = logging.basicConfig()

class CanBus(object):
    def __init__(self, port, baudrate=115200):
        self.ser = serial.Serial(port, baudrate)

    def raw_packet(data):
        self.ser.write("".join([chr(d) for d in data]))

    def can_packet(addr, can_data):
        data = [(addr >> 8) & 0xff, addr & 0xff, 8] + (can_data + [0] * 8)[:8]
        self.raw_packet(data)

class FakeCanBus(CanBus):
    def __init__(self, port, baudrate=115200):
        logger.debug("Setup fake can bus: %s @ %d baud", port, baudrate)

    def raw_packet(data):
        logger.debug("CAN data: %s", map(hex, data))

class RawKeyboard(object):
    def __init__(self, dev_input):
        self.dev = evdev.InputDevice(dev_input)

class AsyncRawKeyboard(asyncore.file_dispatcher):
    def __init__(self, dev_input):
        self.dev = evdev.InputDevice(dev_input)
        super(self, AsyncRawKeyboard).__init__(self.dev)

    def recv(self, ign=None):
        return self.dev.read()

    def handle_read(self):
        for ev in self.recv():
            print evdev.categorize(ev)

class CursedLightUI(object):
    def __init__(self, canbus):
        self.canbus = canbus
        self.scr = curses.initscr()

        self.scr.addstr(10, 10, "Yay curses!")
        self.scr.refresh()

    def loop_forever(self):
        data = []
        while True:
            data.append(scr.getch())
            self.scr.addstr(15, 10, str(data))
            self.scr.refresh()
            if data[-1] == 10:
                break

    def cleanup(self):
        curses.endwin()


if __name__ == "__main__":
    kbd = AsyncRawKeyboard("/dev/input/event4")
    bus = FakeCanBus("/dev/ttyUSB0", 115200)

    ui = CursedLightUI(bus)
    try:
        ui.loop_forever()
    except KeyboardInterrupt:
        # ui.cleanup()
        pass
    ui.cleanup()
