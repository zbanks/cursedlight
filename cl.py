import Queue
import asyncore
import curses
import evdev
import logging
import serial
import threading
import time

logging.basicConfig()
logger = logging.getLogger(__name__)

kbd_evs = Queue.Queue()

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

class Timebase(object):
    fracs = 240
    beats = 4
    def __init__(self):
        self.taps = []
        self.beat = -1
        self.period = 0.5
        self.nextTick = time.time()

    def update(self, t):
        if t>=self.nextTick:
            self.lastTick=self.nextTick
            self.nextFracTick=self.nextTick
            self.nextTick+=self.period
            self.frac=-1
            self.beat+=1
            if self.beat>=self.beats:
                self.beat=0
        if t>=self.nextFracTick:
            real_frac=int(self.fracs*(t-self.lastTick)/self.period)
            if real_frac<self.fracs and real_frac>self.frac:
                self.frac=real_frac
            self.nextFracTick=self.lastTick+float(real_frac+1)*self.period/self.fracs

    def sync(self, t):
        if abs(t-self.nextTick) < abs(t-self.lastTick):
            self.beat=-1
            self.nextTick=t
        else:
            self.beat=0
            self.nextTick=t+self.period

    def tap(self, t):
        self.taps.append(t)
        self.taps=[tap for tap in self.taps if tap > t-2][0:5] # Take away everything older than 2sec or more than 5 history
        diffs=[self.taps[i]-self.taps[i-1] for i in range(1,len(self.taps))] # First differences
        if len(diffs)==0:
                return
        mean=sum(diffs)/len(diffs)
        self.period=mean

    def quantize(self,nearest=2):
        bpm=60.0/self.period
        qbpm=nearest*round(bpm/nearest)
#print "Changed from",bpm,"to",qbpm
        self.period = 60.0 / qbpm
        return qbpm

    @property
    def bpm(self):
        return 60.0 / self.period

    def multiply(self,factor):
        self.period/=factor

    def tick(self):
        self.update(time.time())
        return (self.beat, self.frac)


class RawKeyboard(object):
    def __init__(self, dev_input):
        self.dev = evdev.InputDevice(dev_input)

class AsyncRawKeyboard(threading.Thread):
    def __init__(self, dev_input, kid, grab=False):
        self.dev = evdev.InputDevice(dev_input)
        self.kid = kid
        self.running = True
        self.grab = grab
        if self.grab:
            self.dev.grab()
        threading.Thread.__init__(self)

    def run(self):
        for ev in self.dev.read_loop():
            if not self.running:
                return
            if ev.type == evdev.ecodes.EV_KEY:
                kbd_evs.put((self.kid, ev))

    def stop(self):
        self.running = False
        if self.grab:
            self.dev.ungrab()
        self.join()

class CursedLightUI(object):
    def __init__(self, canbus):
        self.canbus = canbus
        self.tb = Timebase()
        self.running = True

        self.scr = curses.initscr()
        self.scr.addstr(0, 0, "Yay curses!")
        self.scr.refresh()


    def loop_forever(self):
        data = []
        keypress_master = {
            evdev.ecodes.KEY_D: lambda ev: self.tb.multiply(2),
            evdev.ecodes.KEY_E: lambda ev: self.tb.quantize(),
            evdev.ecodes.KEY_H: lambda ev: self.tb.multiply(0.5),
            evdev.ecodes.KEY_Q: lambda ev: self.stop(),
            evdev.ecodes.KEY_R: lambda ev: self.tb.sync(ev.timestamp()),
            evdev.ecodes.KEY_T: lambda ev: self.tb.tap(ev.timestamp()),
        }

        while self.running:
#data.append(self.scr.getch())
            try:
                kid, ev = kbd_evs.get_nowait()
            except Queue.Empty:
                pass
            else:
                if kid == 0 and ev.value == 1: #T down
                    if ev.code in keypress_master:
                        keypress_master[ev.code](ev)

            self.scr.addstr(1, 0, "Tick: %d.%03d" % self.tb.tick())
            self.scr.addstr(2, 0, "BPM: %0.1f" % self.tb.bpm)
            self.scr.refresh()
    
    def stop(self):
        self.running = False

    def cleanup(self):
        curses.endwin()


if __name__ == "__main__":
    kbd = AsyncRawKeyboard("/dev/input/event4", 0)
    bus = FakeCanBus("/dev/ttyUSB0", 115200)
    kbd.start()
    ui = CursedLightUI(bus)
    try:
        ui.loop_forever()
    except KeyboardInterrupt:
        # ui.cleanup()
        pass
    ui.cleanup()
    kbd.stop()
