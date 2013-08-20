import Queue
import asyncore
import collections
import evdev
import glob
import logging
import serial
import threading
import time
import urwid

from effects import *
from evdev import ecodes as E

logging.basicConfig(filename="/tmp/cl.log", level=logging.DEBUG)
logger = logging.getLogger(__name__)

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

# List of all can devices
CAN_DEVICES = dict(reduce(lambda acc, val: acc + val.items(), CAN_DEVICE_GROUPS.values(), []))

# Multicast
CAN_ALL_ADDRESS = 0x0000

class CanBus(object):
    """
    Abstraction for sending data at the hardware level.
    """
    CMD_TICK = 0x80
    CMD_RESET = 0xFF
    CMD_MSG = 0x81

    def __init__(self, port, baudrate=115200):
        self.ser = serial.Serial(port, baudrate)
        self.addresses = {}

    def raw_packet(self, data):
        self.ser.write("".join([chr(d) for d in data]))

    def can_packet(self, addr, can_data):
        if addr not in self.addresses:
            self.addresses[addr] = "(Unknown)"
        can_data = can_data[:8]
        # Format is: [ADDR_H, ADDR_L, LEN, (data), 0xFF]
        data = [(addr >> 8) & 0xff, addr & 0xff, len(can_data)] + can_data + [0xff]
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

class EffectsRunner(object):
    """
    Maintain which effects are running on which devices.
    Keep up with tick events.
    This may end up being CAN-specific???
    I think I need to rexamine my life choices.
    """
    def __init__(self, canbus):
        self.canbus = canbus
        self.last_tick = (0,0)
        self.canbus.send_to_all([self.canbus.CMD_RESET, 0, 0,0,0, 0,0,0])
        # self.effects :: {address ->  {id -> Effect}}
        self.effects = collections.defaultdict(dict)

    def tick(self, tick):
        if tick == self.last_tick:
            return None
        beat, fractick = tick
        if tick[0] != self.last_tick[0]:
            fractick = 0
#self.canbus.send_to_all([self.canbus.CMD_TICK, fractick, 0,0,0, 0,0,0])

        self.last_tick = tick

    def add_device(self, uid, name):
        self.canbus.addresses[uid] = name

    def add_effect(self, effect, addresses, *args, **kwargs):
        """
        Add an effect:
        - Generate an appropriate uid for the effect that isn't already in use
        - Send the effect data out to all the devices
        - Attach the effect to the internal list of effects
        - Return the `effect` instance
        """
        uid = self.get_available_uid(addresses)
        logger.debug("Putting effect %s in uid %02x", str(effect), uid)
        eff = effect(self.canbus, addresses, uid, *args, **kwargs)

        for add in addresses:
            self.effects[add][uid] = eff

        return eff

    def prune_effects(self):
        # Removed stopped effects
        # Sort of shitty, but meh
        for add in addresses:
            for uid in self.effects[add]:
                if self.effects[add][uid].stopped:
                    self.effects[add].pop(uid)

    def get_available_uid(self, addresses):
        # There's only 256 to try and I'm lazy. Brute force ftw
        for i in range(256):
            for add in addresses:
                if i in self.effects[add]:
                    break
            else:
                return i
        # Failure: just overwrite 0xff
        logger.warning("No space for effect, using 0xff")
        return 0xff

class Timebase(object):
    """
    Keep track of timing
    `beat` - 0-indexed number of full beats since the downbeat. Counts up to `self.beats`,
             usually 4. Ex: "ONE two three four ONE two three four" -> [0, 1, 2, 3, 0, 1, 2, 3]
    `frac` - 0-indexed number of fractional beats since the last beat. Counts up to `self.fracs`
             usually 240.
    `tick` - A tuple, `(beat, frac)`.
    """
    # Thanks @ervanalb !
    def __init__(self):
        self.fracs = 240
        self.beats = 4
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

class AsyncRawKeyboard(threading.Thread):
    """
    Poll the "/dev/input/event*" object in a thread and add 
    new events to the `kbd_evts` queue. 
    If `grab` is True, then keyboard events are not propegated to the rest of the system.
    """
    def __init__(self, dev_input, kid, kbd_evts, grab=True):
        self.dev = evdev.InputDevice(dev_input)
        if evdev.ecodes.EV_KEY in self.dev.capabilities() and evdev.ecodes.EV_LED in self.dev.capabilities():
            self.is_keyboard = True
            self.kid = kid
            self.kbd_evs = kbd_evts
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

class Keyboards(object):
    """
    Abstract away all the connected keyboards into a single object
    Exposes `events`, instance of Queue.Queue, containing the events from all 
    the keyboard objects.
    """
    def __init__(self):
        self.events = Queue.Queue()
        self.kbds = []
        # Detect which things in /dev/input/event* are keyboards
        for path in glob.glob("/dev/input/event*"):
            kbd = AsyncRawKeyboard(path, len(self.kbds), self.events, grab=False)
            if not kbd.is_keyboard:
                continue
            self.kbds.append(kbd)
            kbd.start()

    def stop(self):
        [k.stop() for k in self.kbds]

    def set_leds(self, kbd, num=None, caps=None, scroll=None):
        if num is not None:
            num = 1 if num else 0
            kbd.dev.set_led(evdev.ecodes.LED_NUML, num)
        if caps is not None:
            caps = 1 if caps else 0
            kbd.dev.set_led(evdev.ecodes.LED_CAPSL, caps)
        if scroll is not None:
            scroll = 1 if scroll else 0
            kbd.dev.set_led(evdev.ecodes.LED_SCROLLL, scroll)

    def set_all_leds(self, **kwargs):
        for k in self.kbds:
            self.set_leds(k, **kwargs)

class CustomSelectEventLoop(urwid.SelectEventLoop):
    """
    The built-in `urwid.SelectEventLoop` only updates when something has changed.
    Fix this by keeping `self._did_something` True.
    """
    def _loop(self):
        self._did_something = True
        super(CustomSelectEventLoop, self)._loop()
        self.custom_function()

class CursedLightUI(object):
    """
    My abstraction for what belongs in this class vs. other classes has totally
    degraded and shifted around. This class is super messy :(
    """
    palette = [
        ('bpm', '', '', '', '#333', '#ddd'),
        ('bpm_text', '', '', '', '#333', '#ddd'),
        ('bg', '', '', '', '#333', '#ddd'),
    ]
    def __init__(self, keyboards, effects_runner):
        self.tb = Timebase()
        self.keyboards = keyboards
        self.effects_runner = effects_runner
        self.running = True

        self.last_tick = (0, 0)

        evloop = CustomSelectEventLoop()
        evloop.custom_function = lambda: self.idle_loop()

        placeholder = urwid.SolidFill()
        self.loop = urwid.MainLoop(placeholder, self.palette, event_loop=evloop)
        self.loop.screen.set_terminal_properties(colors=256)
        self.loop.widget = urwid.AttrMap(placeholder, 'bg')
        self.loop.widget.original_widget = urwid.Filler(urwid.Pile([]))
#self.loop.event_loop.enter_idle(lambda: self.idle_loop())

        self.pile = self.loop.widget.base_widget

        self.status = urwid.Text(('status', 'CursedLight - Debug'), align='center')
        self.bpm = urwid.Text("", align='center')

        for item in [self.status, self.bpm]:
            self.pile.contents.append((item, self.pile.options()))

        self.keypress_master = {
            E.KEY_D: lambda ev: self.tb.multiply(2),
            E.KEY_E: lambda ev: self.tb.quantize(),
            E.KEY_H: lambda ev: self.tb.multiply(0.5),
            E.KEY_Q: lambda ev: self.stop(),
            E.KEY_R: lambda ev: self.tb.sync(ev.timestamp()),
            E.KEY_T: lambda ev: self.tb.tap(ev.timestamp()),
            E.KEY_Z: lambda ev: self.effects_runner.add_effect(SolidColorEffect, [0], HSVA['red']),
        }

    def loop_forever(self):
        self.loop.run()

    def idle_loop(self):
        tick = self.tb.tick()
        self.bpm.set_text([('bpm_text', 'Tick: '), ('bpm', '{0}.{1:03d}'.format(*tick)),
                           ('bpm_text', 'BPM: '), ('bpm', '{: <6.01f}'.format(self.tb.bpm))])
        if tick[1] < 10:
            self.keyboards.set_all_leds(caps=tick[0] % 2)

        self.effects_runner.tick(tick)

        while not self.keyboards.events.empty():
            try:
                kid, ev = self.keyboards.events.get_nowait()
            except Queue.Empty:
                pass
            else:
                if kid == 0 and ev.value == 1: #T down
                    if ev.code in self.keypress_master:
                        self.keypress_master[ev.code](ev)

        self.last_tick = tick

    def stop(self):
        raise urwid.ExitMainLoop()

    def cleanup(self):
        pass

def main():
    keyboards = Keyboards()
    try:
        print "Found %d keyboards" % len(keyboards.kbds)
#keyboards.set_leds(True, True, True)
        time.sleep(0.5)
#keyboards.set_leds(False,False,False)
        bus = FakeCanBus("/dev/ttyUSB0", 115200)
        effects_runner = EffectsRunner(bus)
        [effects_runner.add_device(*dev) for dev in CAN_DEVICES.items()]
    except Exception:
        keyboards.stop()
        raise

    try:
        ui = CursedLightUI(keyboards, effects_runner)
        ui.loop_forever()
    except KeyboardInterrupt:
        pass
    except Exception:
        keyboards.stop()
        ui.cleanup()
        raise
    keyboards.stop()
    ui.cleanup()

if __name__ == "__main__":
    main()
