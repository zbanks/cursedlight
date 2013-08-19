import Queue
import asyncore
import evdev
import logging
import serial
import threading
import time
import glob
import urwid

logging.basicConfig()
logger = logging.getLogger(__name__)

class CanBus(object):
    def __init__(self, port, baudrate=115200):
        self.ser = serial.Serial(port, baudrate)
        self.addresses = {}

    def raw_packet(data):
        self.ser.write("".join([chr(d) for d in data]))

    def can_packet(addr, can_data):
        if addr not in self.addresses:
            self.addresses[addr] = "(Unknown)"
        data = [(addr >> 8) & 0xff, addr & 0xff, 8] + (can_data + [0] * 8)[:8]
        self.raw_packet(data)

    def send_to_all(can_data):
        for addr in self.addresses:
            self.can_packet(addr, can_data)

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
    def __init__(self, dev_input, kid, kbd_evs, grab=False):
        self.dev = evdev.InputDevice(dev_input)
        if evdev.ecodes.EV_KEY in self.dev.capabilities() and evdev.ecodes.EV_LED in self.dev.capabilities():
            self.is_keyboard = True
            self.kid = kid
            self.kbd_evs = kbd_evs
            self.running = True
            self.grab = grab
            if self.grab:
                self.dev.grab()
            threading.Thread.__init__(self)
        else:
            self.is_keyboard = False
            self.running = False

    def run(self):
        for ev in self.dev.read_loop():
            if not self.running:
                return
            if ev.type == evdev.ecodes.EV_KEY:
                self.kbd_evs.put((self.kid, ev))

    def stop(self):
        self.running = False
        if self.grab:
            self.dev.ungrab()
        self.join()

class CursedLightUI(object):
    palette = [
        ('bpm', '', '', '', '#333', '#ddd'),
        ('bpm_text', '', '', '', '#333', '#ddd'),
        ('bg', '', '', '', '#333', '#ddd'),
    ]
    def __init__(self, canbus, kbd_evs):
        self.canbus = canbus
        self.kbd_evs = kbd_evs
        self.tb = Timebase()
        self.running = True

        placeholder = urwid.SolidFill()
        self.loop = urwid.MainLoop(placeholder, self.palette)
        self.loop.screen.set_terminal_properties(colors=256)
        self.loop.widget = urwid.AttrMap(placeholder, 'bg')
        self.loop.widget.original_widget = urwid.Filler(urwid.Pile([]))
        self.loop.event_loop.enter_idle(lambda: self.idle_loop())

        self.pile = self.loop.widget.base_widget

        self.status = urwid.Text(('status', 'CursedLight - Debug'), align='center')
        self.bpm = urwid.Text("", align='center')

        for item in [self.status, self.bpm]:
            self.pile.contents.append((item, self.pile.options()))

        self.keypress_master = {
            evdev.ecodes.KEY_D: lambda ev: self.tb.multiply(2),
            evdev.ecodes.KEY_E: lambda ev: self.tb.quantize(),
            evdev.ecodes.KEY_H: lambda ev: self.tb.multiply(0.5),
            evdev.ecodes.KEY_Q: lambda ev: self.stop(),
            evdev.ecodes.KEY_R: lambda ev: self.tb.sync(ev.timestamp()),
            evdev.ecodes.KEY_T: lambda ev: self.tb.tap(ev.timestamp()),
        }

    def loop_forever(self):
        self.loop.run()

    def idle_loop(self):
        try:
            kid, ev = self.kbd_evs.get_nowait()
        except Queue.Empty:
            pass
        else:
            if kid == 0 and ev.value == 1: #T down
                if ev.code in self.keypress_master:
                    self.keypress_master[ev.code](ev)

        self.bpm.set_text([('bpm_text', 'Tick: '), ('bpm', '{0}.{1:03d}'.format(*self.tb.tick())),
                           ('bpm_text', 'BPM: '), ('bpm', '{: <6.01f}'.format(self.tb.bpm))])
        """
        self.scr.addstr(1, 0, "Tick: {0}.{1:03d}".format(*self.tb.tick()))
        self.scr.addstr(2, 0, "BPM:  {: <6.01f}".format(self.tb.bpm))
        self.scr.refresh()
        """
    
    def stop(self):
        raise urwid.ExitMainLoop()

    def cleanup(self):
        pass


def setup_keyboards():
    kbd_evs = Queue.Queue()
    kbds = []
    for path in glob.glob("/dev/input/event*"):
        kbd = AsyncRawKeyboard(path, len(kbds), kbd_evs, grab=False)
        if not kbd.is_keyboard:
            continue
        kbds.append(kbd)
        kbd.start()
    return kbd_evs, kbds

def stop_keyboards(kbds):
    [k.stop() for k in kbds]

def main():
    kbd_evs, keyboards = setup_keyboards()
    print "Found %d keyboards" % len(keyboards)
    time.sleep(0.5)
    bus = FakeCanBus("/dev/ttyUSB0", 115200)
    ui = CursedLightUI(bus, kbd_evs)
    try:
        ui.loop_forever()
    except KeyboardInterrupt:
        pass
    except Exception:
        ui.cleanup()
        stop_keyboards(keyboards)
        raise
    ui.cleanup()
    stop_keyboards(keyboards)

if __name__ == "__main__":
    main()
