import Queue
import asyncore
import collections
import evdev
import logging
import serial
import sys
import threading
import traceback
import time
import urwid

from effects import *
from config import *
from curtain import BeatBlaster
from evdev import ecodes as E

logging.basicConfig(filename="/tmp/cl.log", level=logging.DEBUG)
logger = logging.getLogger(__name__)

def exception_handler(type, value, tb):
    logger.exception("Uncaught exception: {0}".format(str(value)))
    logger.exception(traceback.print_traceback(tb))

sys.excepthook = exception_handler


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
        self.effects_named = {}
        if IRON_CURTAIN_ENABLED:
            self.iron_curtain = BeatBlaster(IRON_CURTAIN_ADDR)

    def tick(self, tick):
        if tick == self.last_tick:
            return None
        beat, fractick = tick
        if tick[0] != self.last_tick[0]:
            fractick = 0
            if IRON_CURTAIN_ENABLED:
                logger.debug("beat")
                self.iron_curtain.beat(beat)
        if IRON_CURTAIN_ENABLED:
            self.iron_curtain.sub_beat(beat, fractick)

        tick = (beat, fractick)
        # XXX: disabled to not clog up the logs
        if SEND_BEATS and (fractick % FRACTICK_FRAC) == 0:
            self.canbus.send_to_all([self.canbus.CMD_TICK, fractick])
        
        # Maybe the effects want to do something?
        for eff in self.effects_named.values():
            eff.tick(tick)

        self.last_tick = tick

    def reset_device(self, uid):
        self.canbus.can_packet(uid, [self.canbus.CMD_RESET])
        effs = self.effects[uid]
        for eff in effs.values():
            try:
                self.effects_named.pop(eff.name)
            except KeyError:
                pass
        self.effects[uid] = {}

    def add_device(self, uid, name):
        self.canbus.addresses[uid] = name

    def add_effect(self, name, effect, addresses, *args, **kwargs):
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
        eff.name = name

        for add in addresses:
            self.effects[add][uid] = eff

        self.effects_named[name] = eff
        return eff

    def msg_effect(self, name, data):
        eff = self.effects_named[name]
        eff.msg(data)

    def stop_effect(self, name):
        if name not in self.effects_named:
            return None
        eff = self.effects_named.pop(name)
        logger.debug(self.effects)

        for add in eff.device_ids:
            self.effects[add].pop(eff.unique_id)

        eff.stop()
        return eff

    def effect_name_exists(self, name):
        return name in self.effects_named

    def prune_effects(self):
        # Removed stopped effects
        # Sort of shitty, but meh
        logger.warning("DEPRECATED: prune_effects")
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

    def nudge(self, t):
        self.period = 60.0 / (self.bpm + t)

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
        if self.bpm > 500 or self.bpm < 20:
            # Undo; set limits
            self.period *= factor

    def tick(self):
        self.update(time.time())
        return (self.beat, self.frac)

class AsyncRawKeyboard(threading.Thread):
    """
    Poll the "/dev/input/event*" object in a thread and add 
    new events to the `kbd_evts` queue. 
    If `grab` is True, then keyboard events are not propegated to the rest of the system.
    """
    # Why aren't these defined in evdev.ecodes!? :(

    KEY_UP = 0
    KEY_DOWN = 1
    KEY_HOLD = 2
    def __init__(self, dev_input, kid, kbd_evts, grab=True):
        self.dev = evdev.InputDevice(dev_input)
        if evdev.ecodes.EV_KEY in self.dev.capabilities() and evdev.ecodes.EV_LED in self.dev.capabilities():
            self.is_keyboard = True
            self.kid = kid
            self.kbd_evs = kbd_evts
            self.running = True
            self.grab = grab
            self.pressed = set()
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
                if ev.value == self.KEY_DOWN:
                    self.pressed.add(ev.code)
                if ev.value == self.KEY_UP:
                    self.pressed.discard(ev.code)
                self.kbd_evs.put((self.kid, ev, self.pressed))

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
        for path in evdev.list_devices()[::-1]:
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

class IronCurtainUI(object):
    def __init__(self, change_scene):
        self.name = IRON_CURTAIN
        self.pile = urwid.Pile([])
        bx = urwid.Filler(self.pile, valign='top')
        self.base = urwid.LineBox(bx)

        self.enable = urwid.CheckBox('Enable', True)
        self.mute = urwid.CheckBox('Mute')
        self.freeze = urwid.CheckBox('Freeze')
        self.reset = urwid.Button('Reset')

        self.div = urwid.Divider("-")

        self.kbd_test = urwid.Text('')
        self.dev_group = urwid.Text(self.name, align='center')
        self.effects = urwid.Pile([])


        for w in [self.dev_group, self.kbd_test, self.enable, self.mute, self.freeze, self.reset, self.div, self.effects]:
            self.pile.contents.append((w, self.pile.options()))

        def radio_change(btn, new_state, scene_num):
            if new_state == True:
                change_scene(scene_num)

        scene_group = []
        for i, scene in enumerate(IRON_CURTAIN_SCENES):
            btn = urwid.RadioButton(scene_group, scene, on_state_change=radio_change, state=i == 0, user_data=i)
            self.pile.contents.append((btn, self.pile.options()))

class CANDeviceGroupUI(object):
    def __init__(self, name):
        self.name = name
        self.pile = urwid.Pile([])
        bx = urwid.Filler(self.pile, valign='top')
        self.base = urwid.LineBox(bx)

        self.options = urwid.Columns([])
        self.enable = urwid.CheckBox('Enable', True)
        self.mute = urwid.CheckBox('Mute')
        self.freeze = urwid.CheckBox('Freeze')
        self.reset = urwid.Button('Reset')
        self.kbd_test = urwid.Text('')

        self.dev_group = urwid.Text('Dev group', align='center')
        self.effects = urwid.Pile([])

        for w in [self.mute, self.freeze, self.reset, self.kbd_test]:
            self.options.contents.append((w, self.options.options()))

        for w in [self.dev_group, self.options, self.effects]:
            self.pile.contents.append((w, self.pile.options()))

class CursedLightUI(object):
    """
    My abstraction for what belongs in this class vs. other classes has totally
    degraded and shifted around. This class is super messy :(
    """
    palette = [
        ('bpm', '', '', '', '#333', '#ddd'),
        ('bpm_text', '', '', '', '#333', '#ddd'),
        ('bg', '', '', '', '#333', '#fff'),
        ('status', '', '', '', '#333', '#ddd'),
        ('status', '', '', '', '#333', '#ddd'),
    ]
    def __init__(self, keyboards, effects_runner):
        self.tb = Timebase()
        self.keyboards = keyboards
        self.effects_runner = effects_runner
        self.running = True

        self.last_tick = (0, 0)

        evloop = CustomSelectEventLoop()
        evloop.custom_function = lambda: self.idle_loop()

        self.kbd_event_handlers = collections.defaultdict(list)
        self.kbd_event_handlers[KEYBOARD_MAP['MASTER']].append(self.master_kbd_handler)

        placeholder = urwid.SolidFill()
        self.loop = urwid.MainLoop(placeholder, self.palette, event_loop=evloop)
        self.loop.screen.set_terminal_properties(colors=256)
        self.loop.widget = urwid.AttrMap(placeholder, 'bg')

        self.header = urwid.Columns([])
        self.footer = urwid.Columns([])
        self.body = urwid.Columns([])

        self.loop.widget.original_widget = urwid.Frame(body=self.body, header=self.header, footer=self.footer)

        self.status = urwid.Text(('status', 'CursedLight - Debug'), align='left')
        self.device_status = urwid.Text(('status', 'Devices'), align='right')
        self.bpm = urwid.Text("", align='left')
        self.ticker = urwid.Text("", align='right')

        self.header.contents.append((self.status, self.header.options()))
        self.header.contents.append((self.device_status, self.header.options()))
        self.footer.contents.append((self.bpm, self.footer.options()))
        self.footer.contents.append((self.ticker, self.footer.options()))

        self.setup_devices()

        self.keypress_master = {
            E.KEY_E: lambda ev: self.tb.quantize(),
            E.KEY_G: lambda ev: self.tb.multiply(2),
            E.KEY_H: lambda ev: self.tb.multiply(0.5),
            E.KEY_Q: lambda ev: self.stop(),
            E.KEY_R: lambda ev: self.tb.sync(ev.timestamp()),
            E.KEY_T: lambda ev: self.tb.tap(ev.timestamp()),
            E.KEY_F: lambda ev: self.tb.nudge(1),
            E.KEY_D: lambda ev: self.tb.nudge(-1),
        }

    def master_kbd_handler(self, event):
        kid, ev, pressed = event
        if ev.value == 1: #T down
            if ev.code in self.keypress_master:
                self.keypress_master[ev.code](ev)

    def iron_curtain_kbd_handler(self, event, icui):
        kid, ev, pressed = event
        if ev.value == 1 and ev.code == E.KEY_G:
            self.effects_runner.iron_curtain.change_scene(0)

    def iron_curtain_ui_handler(self, new_scene):
        logger.debug("Change Iron Curtain scene: %d", new_scene)
        self.effects_runner.iron_curtain.change_scene(new_scene)

    def can_device_kbd_handler(self, event, dgui, devs):
        def toggle_effect(eff_name, *args, **kwargs):
            if self.effects_runner.effect_name_exists(eff_name):
                eff = self.effects_runner.stop_effect(eff_name)
                dgui.effects.contents = filter(lambda x: x[0] != eff.ui.base, dgui.effects.contents)
                return False 
            else:
                eff = self.effects_runner.add_effect(eff_name, *args, **kwargs)
                dgui.effects.contents.append((eff.ui.base, dgui.effects.options()))
                return True

        kid, ev, pressed = event
        if ev.value == 1:
            dgui.kbd_test.set_text('KEY ({})'.format(kid))
        elif ev.value == 0:
            dgui.kbd_test.set_text('')

        speed_keys = {
            E.KEY_RIGHTSHIFT: 0,
            E.KEY_RIGHTCTRL: 1,
            E.KEY_RIGHTALT: 2,
            # Default is 3
            E.KEY_LEFTALT: 4,
            E.KEY_LEFTCTRL: 5,
            E.KEY_LEFTSHIFT: 6
        }

        solid_color_keys = {
            E.KEY_Z: 'red',
            E.KEY_X: 'orange',
            E.KEY_C: 'yellow',
            E.KEY_V: 'green',
            E.KEY_B: 'cyan',
            E.KEY_N: 'blue',
            E.KEY_M: 'purple',
            E.KEY_COMMA: 'white',
            E.KEY_DOT: 'black',
        }

        fadein_color_keys = {
            E.KEY_LEFTBRACE: 'black',
            E.KEY_RIGHTBRACE: 'white',
        }

        pulse_color_keys = {
            E.KEY_Q: 'red',
            E.KEY_W: 'orange',
            E.KEY_E: 'yellow',
            E.KEY_R: 'green',
            E.KEY_T: 'cyan',
            E.KEY_Y: 'blue',
            E.KEY_U: 'purple',
            E.KEY_I: 'white',
            E.KEY_O: 'black',
        }

        rate = 3
        for s in speed_keys:
            if s in pressed:
                rate = speed_keys[s]
                break
        if E.KEY_SPACE in pressed:
            rate *= -1

        if ev.value == 0: # Key up
            if ev.code in solid_color_keys:
                color_name = solid_color_keys[ev.code]
                eff_name = "{} solid {}".format(dgui.name, color_name)
                eff = self.effects_runner.stop_effect(eff_name)
                if eff is not None:
                    dgui.effects.contents = filter(lambda x: x[0] != eff.ui.base, dgui.effects.contents)
        if ev.value == 1: # Key down
            if ev.code in solid_color_keys:
                if E.KEY_LEFTMETA in pressed:
                    color_name = solid_color_keys[ev.code]
                    eff_name = "{} solid {}".format(dgui.name, color_name)
                    toggle_effect(eff_name, SolidColorEffect, devs.keys(), RGBA[color_name])
                else:
                    # Strobe
                    color_name = solid_color_keys[ev.code]
                    eff_name = "{} strobe {}".format(dgui.name, color_name)
                    toggle_effect(eff_name, StrobeColorEffect, devs.keys(), RGBA[color_name], rate=rate)
            elif ev.code in fadein_color_keys:
                color_name = fadein_color_keys[ev.code]
                eff_name = "{} fadein {}".format(dgui.name, color_name)
                rate = max(0, rate - 2)
                toggle_effect(eff_name, FadeinEffect, devs.keys(), color_rgba=RGBA[color_name], rate=rate)
            elif ev.code in pulse_color_keys:
                color_name = pulse_color_keys[ev.code]
                if E.KEY_LEFTMETA in pressed:
                    eff_name = "{} swipe {}".format(dgui.name, color_name)
                    toggle_effect(eff_name, SwipeColorEffect, devs.keys(), color_rgba=RGBA[color_name], rate=rate)
                else:
                    eff_name = "{} pulse {}".format(dgui.name, color_name)
                    toggle_effect(eff_name, PulseColorEffect, devs.keys(), color_rgba=RGBA[color_name], rate=rate)
            elif ev.code == E.KEY_A:
                eff_name = "{} flash_rainbow".format(dgui.name)
                toggle_effect(eff_name, FlashRainbowEffect, devs.keys())
            elif ev.code == E.KEY_S:
                eff_name = "{} smooth_rainbow".format(dgui.name)
                toggle_effect(eff_name, RainbowEffect, devs.keys(), l_period=rate)
            elif ev.code == E.KEY_L:
                eff_name = "{} pulse black".format(dgui.name)
                toggle_effect(eff_name, PulseColorEffect, devs.keys(), RGBA["black"])
            elif ev.code == E.KEY_END:
                # Reset
                [self.effects_runner.reset_device(uid) for uid in devs.keys()]
                dgui.effects.contents = []


    def setup_devices(self):
        self.dguis = {}
        def kbd_handler(dgui, devs): return lambda ev: self.can_device_kbd_handler(ev, dgui, devs)
        for devgroup, devs in CAN_DEVICE_GROUPS.items():
            dgui = CANDeviceGroupUI(devgroup)
            self.body.contents.append((dgui.base, self.body.options()))
            dgui.dev_group.set_text("{} ({})".format(devgroup, len(devs)))
            self.dguis[devgroup] = dgui
            self.kbd_event_handlers[KEYBOARD_MAP[devgroup]].append(
                    kbd_handler(dgui, devs)
            )
        self.device_status.set_text("Devices: {} CAN".format(len(self.effects_runner.canbus.addresses)-1))
        if IRON_CURTAIN_ENABLED:
            icui = IronCurtainUI(lambda sc: self.iron_curtain_ui_handler(sc))
            self.body.contents.append((icui.base, self.body.options()))
            self.dguis[IRON_CURTAIN] = icui
            self.kbd_event_handlers[KEYBOARD_MAP[IRON_CURTAIN]].append(
                (lambda ui: lambda ev: self.iron_curtain_kbd_handler(ev, ui))(icui)
            )
            self.device_status.set_text("Devices: {} CAN + Iron Curtain".format(len(self.effects_runner.canbus.addresses)-1))

    def loop_forever(self):
        self.loop.run()

    def idle_loop(self):
        tick = self.tb.tick()
        self.ticker.set_text([('bpm_text', 'Tick: '), ('bpm', '{0}.{1:03d}'.format(*tick))])
        self.bpm.set_text([('bpm_text', 'BPM: '), ('bpm', '{: <6.01f}'.format(self.tb.bpm))])
        if tick[1] < 10:
            self.keyboards.set_all_leds(caps=tick[0] == 0)

        self.effects_runner.tick(tick)

        while not self.keyboards.events.empty():
            try:
                event = self.keyboards.events.get_nowait()
                kid, ev, pressed = event
                if ev.value == 1:
                    logger.debug("KEY: %s, %s, %s", kid, evdev.categorize(ev), map(lambda x: E.KEY[x], pressed))
            except Queue.Empty:
                pass
            else:
                for ev_handler in self.kbd_event_handlers[kid]:
                    ev_handler(event)

        self.last_tick = tick

    def stop(self):
        raise urwid.ExitMainLoop()

    def cleanup(self):
        pass

def main():
    keyboards = Keyboards()
    try:
        print "Found %d keyboards" % len(keyboards.kbds)
        logger.debug("Found %d keyboards" % len(keyboards.kbds))
#keyboards.set_leds(True, True, True)
        time.sleep(0.5)
#keyboards.set_leds(False,False,False)
        bus = FakeCanBus("/dev/ttyUSB0", 115200)
#bus = CanBus("/dev/ttyUSB0", 115200)
        effects_runner = EffectsRunner(bus)
        [effects_runner.add_device(*dev) for dev in CAN_DEVICES.items()]
        ui = CursedLightUI(keyboards, effects_runner)
    except Exception:
        keyboards.stop()
        raise

    try:
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
