import Queue
import collections
import evdev
import logging
import sys
import threading
import traceback
import urwid

from effects import *
from config import *
from inputs import *
from timing import *
from devices import *
from evdev import ecodes as E

logging.basicConfig(filename="/tmp/cl.log", level=logging.DEBUG)
logger = logging.getLogger(__name__)

def exception_handler(type, value, tb):
    logger.exception("Uncaught exception: {0}".format(str(value)))
    logger.exception(traceback.print_traceback(tb))

sys.excepthook = exception_handler


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
        for i, gc in enumerate(GLOBAL_CALIBRATION):
            self.canbus.send_to_all([self.canbus.CMD_PARAM, i, int(255.0 * gc) ,0,0, 0,0,0])
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
            if IRON_CURTAIN_ENABLED and fractick % IRON_CURTAIN_FT == 0:
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
        for i, gc in enumerate(CAN_DEVICE_CALIBRATION.get(uid, GLOBAL_CALIBRATION)):
            self.canbus.send_to_all([self.canbus.CMD_PARAM, i, int(255.0 * gc) ,0,0, 0,0,0])
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

class Pattern(object):
    SEQ_LEN = 32
    CHANNELS = 8
    all_titles = set()
    def __init__(self, name=None):
        self.data = [([0] * self.SEQ_LEN) for i in range(self.CHANNELS)]
        self.channels = [[]  for i in range(self.CHANNELS)]
        self.channels_muted = [False] * self.CHANNELS
        self.speed = 1

        if name is None:
            name = "Pattern #%d" % (len(self.all_titles) + 1)
        while name in self.all_titles:
            name += "_"
        self.all_titles.add(name)
        self.title = name

    def get_channel_description(self, i):
        channel = self.channels(i)
        return "Channel %d: %s" % (i+1, str(channel)) #TODO

    def serialize(self):
        return {
            "data": self.data,
            "channels": self.channels,
            "title": self.title,
            "_seqlen": self.SEQ_LEN,
            "_channels": self.CHANNELS
        }
    @classmethod
    def deserialize(cls, d):
        # Maybe there should be more checks. Don't mess around too much
        if d["_seqlen"] != self.SEQ_LEN:
            raise Exception("Unable to deserialize pattern; mismatched seqlen (should be %d)" % self.SEQ_LEN)
        if d["_channels"] != self.CHANNELS:
            raise Exception("Unable to deserialize pattern; mismatched channels (should be %d)" % self.CHANNELS)
        p = cls(name=d["title"])
        p.data = d["data"]
        p.channels = d["channels"]
        return p

class SequencingGrid(object):
    def __init__(self): 
        self.pattern = None

        self.grid_rows = []
        self.grid_texts = []
        self.grid_descs = []
        self.grid_mutes = []

        for i in range(Pattern.CHANNELS):
            txt = urwid.Text("#" * Pattern.SEQ_LEN)
            desc = urwid.Text("-")
            mute = urwid.CheckBox("", state=False)
            row = urwid.Columns([(Pattern.SEQ_LEN, txt), (4, mute), desc])
            self.grid_rows.append(row)
            self.grid_texts.append(txt)
            self.grid_descs.append(desc)
            self.grid_mutes.append(mute)

        self.timing_row = urwid.Text('^   .   ' * (Pattern.SEQ_LEN / 8))
        self.grid = urwid.Pile([('pack', r) for r in  (self.grid_rows + [self.timing_row])])

        self.title = urwid.Edit(caption="Title:", edit_text="Pattern 1")
        
        speed_btns = []
        for sp in [1, 2, 4]:
            urwid.RadioButton(speed_btns, "%dx Speed" % sp, user_data=sp)
        self.details = urwid.Pile([self.title] + speed_btns)
        self.content = urwid.Columns([('weight', 3, self.grid), ('weight', 1, self.details)])
        self.base = urwid.LineBox(self.content)

    def load_pattern(self, pattern=None):
        def val_to_sym(v):
            # Convert a value at a point in time on a channel to a single char
            SYMBOLS = {
                0: "-",
                1: "#",
                2: "+",
                3: "'",
                "t": ">",
                "else": "?"
            }
            return SYMBOLS.get(v, SYMBOLS["else"])
        # Load pattern passed in as argument
        # Otherwise use self.pattern & refresh, otherwise exit
        if pattern is not None:
            self.pattern = pattern
        elif self.pattern is not None:
            pattern = self.pattern
        else:
            return

        # Populate the grid channels & controls
        for i in range(pattern.CHANNELS):
            self.grid_texts[i].set_text(''.join(map(pattern.data[i], val_to_sym)))
            self.grid_descs[i].set_text(pattern.get_channel_description(i))
            self.grid_mutes[i].set_state(pattern.channel_muted[i], do_callback=False)
        self.title.set_text("Title: %s" % pattern.title)
        for sbtn in speed_btns:
            if pattern.speed == sbtn.user_data:
                sbtn.toggle_state()
                break


class PatternGrid(object):
    def __init__(self, patterns):
        self.patterns = patterns

        self.new_pattern = urwid.LineBox(urwid.Padding(urwid.Text("New")))
        self.content = urwid.GridFlow([], 16, 1, 1, 'center')
        self.base = urwid.LineBox(urwid.Filler(self.content))

        self.rebuild_buttons()
    
    def make_button(self, pattern):
#return urwid.LineBox(urwid.SolidFill("."))
        return urwid.LineBox(urwid.Padding(urwid.Text(pattern.title)))

    def make_new_button(self):
        return urwid.LineBox(urwid.Padding(urwid.Text("New")))

    def rebuild_buttons(self):
        btns = []
        for pattern in self.patterns:
            btns.append((self.make_button(pattern), self.content.options()))
        btns.append((self.make_new_button(), self.content.options()))
        self.content.contents = btns


class SettingsBox(object):
    def __init__(self):
        self.content = urwid.Pile([])
        self.base = urwid.LineBox(self.content)

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
        self.center = urwid.Pile([])

        self.patterns = [Pattern() for i in range(10)]

        self.seqgrid = SequencingGrid()
        self.patgrid = PatternGrid(self.patterns)
        self.settings = SettingsBox()
        self.vbody = urwid.Pile([('pack', self.seqgrid.base), self.patgrid.base])
        self.body = urwid.Columns([('weight', 5, self.vbody), ('weight', 1, self.settings.base)])

        self.loop.widget.original_widget = urwid.Frame(body=self.body, header=self.header, footer=self.footer)

        self.status = urwid.Text(('status', 'CursedLight - Debug'), align='left')
        self.device_status = urwid.Text(('status', 'Devices'), align='right')
        self.bpm = urwid.Text("", align='left')
        self.ticker = urwid.Text("", align='right')

        self.header.contents.append((self.status, self.header.options()))
        self.header.contents.append((self.device_status, self.header.options()))
        self.footer.contents.append((self.bpm, self.footer.options()))
        self.footer.contents.append((self.ticker, self.footer.options()))

#self.setup_devices()

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
        pulse_color_keys = {
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
#toggle_effect(eff_name, StrobeColorEffect, devs.keys(), RGBA[color_name], rate=rate)
                    toggle_effect(eff_name, StrobeEffect, devs.keys(), RGBA[color_name], rate=rate)
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
