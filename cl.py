import Queue
import collections
import copy
import evdev
import logging
import sys
import threading
import traceback
import urwid

from evdev import ecodes as E

from channels import *
from config import *
from devices import *
from effects import *
from inputs import *
from timing import *


logging.basicConfig(filename="/tmp/cl.log", level=logging.DEBUG)
logger = logging.getLogger(__name__)

def exception_handler(type, value, tb):
    logger.exception("Uncaught exception: {0}".format(str(value)))
    logger.exception(traceback.print_traceback(tb))

sys.excepthook = exception_handler
debug = lambda s: s


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
    def __init__(self, device_manager, name=None):
        self.device_manager = device_manager
        self.data = [([0] * self.SEQ_LEN) for i in range(self.CHANNELS)]
        self.channels = [[]  for i in range(self.CHANNELS)]
        self.channels_muted = [False] * self.CHANNELS
        self.speed = 1
        self.active = False
        self.keybinding = (None, None)

        if name is None:
            name = "Pattern #%d" % (len(self.all_titles) + 1)
        while name in self.all_titles:
            name += "_"
        self.all_titles.add(name)
        self.title = name

    def get_channel_description(self, i):
        channel = self.channels[i]
        return "Channel %d: %s" % (i+1, str(channel)) #TODO

    def tap(self, channel, beat, width=1, keyboard=True):
        # User input to change pattern @ (channel, beat)
        v = self.data[channel][beat]
        self.data[channel][beat] = 1 - v

    def toggle(self):
        self.active = not self.active
        if self.active:
            for channel in self.channels:
                if channel is not None:
                    channel.start()
                    debug("STARTED")
        else:
            for channel in self.channels:
                if channel is not None:
                    channel.stop()

    def tick(self, time):
        beat, tick = time
        step = Timebase.scale(time, self.SEQ_LEN)
        for channel, data in zip(self.channels, self.data):
            if channel is not None:
                #debug(data[step])
                channel.tick(time, data[step])

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
        if d["_seqlen"] != cls.SEQ_LEN:
            raise Exception("Unable to deseri1alize pattern; mismatched seqlen (should be %d)" % self.SEQ_LEN)
        if d["_channels"] != cls.CHANNELS:
            raise Exception("Unable to deserialize pattern; mismatched channels (should be %d)" % self.CHANNELS)
        p = cls(name=d["title"])
        p.data = copy.deepcopy(d["data"])
        p.channels = copy.deepcopy(d["channels"])
        return p

    @classmethod
    def new_template(cls, device_manager):
        p = cls(device_manager, name="New Pattern")
        p.data = [[0] * 32 for i in range(8)]
        p.channels = [None] * 8
        return p

EXAMPLE_PATTERN_SERIALIZED = {
    #"data": [[1,0,0,0] * 8, [1,0] * 16, [0,0,1,0,0,1,0,1] * 4] + [[0] * 32 for i in range(5)],
    "data": [[0] * 32 for i in range(8)],
    "channels": ["Ch"] * 8,
    "title": "Ex. Pattern",
    "_seqlen": Pattern.SEQ_LEN,
    "_channels": Pattern.CHANNELS
}

class PatternText(urwid.WidgetWrap):
    def __init__(self, pattern=None, index=None, data=None):
        if data is not None:
            data = []
        self.data = data
        self.pattern = pattern
        self.index = index

        super(PatternText, self).__init__(urwid.Text(''))
        self.update()

    def update(self, pattern=None, index=None, data=None):
        if pattern is not None:
            self.pattern = pattern
        if index is not None and self.pattern is not None:
            self.index = index
        elif data is not None:
            self.data = data
            self.index = None

        if self.index is not None and self.pattern is not None:
            self.data = self.pattern.data[self.index]

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

        if self.data is not None:
            self._w.set_text(''.join(map(val_to_sym, self.data)))

    def mouse_event(self, size, event, button, col, row, focus):
        if self.pattern is not None and self.index is not None:
            if event in ("mouse press", "mouse drag"):
                self.pattern.tap(self.index, col, keyboard=False)
                self.update()

class SequencingGrid(object):
    # Keys 1-8; Q-I; A-K; Z-, form a grid 
    KEYS_GRID = {
        E.KEY_1: (0, 0), E.KEY_2: (0, 1), E.KEY_3: (0, 2), E.KEY_4: (0, 3),
        E.KEY_5: (0, 4), E.KEY_6: (0, 5), E.KEY_7: (0, 6), E.KEY_8: (0, 7),

        E.KEY_Q: (1, 0), E.KEY_W: (1, 1), E.KEY_E: (1, 2), E.KEY_R: (1, 3),
        E.KEY_T: (1, 4), E.KEY_Y: (1, 5), E.KEY_U: (1, 6), E.KEY_I: (1, 7),

        E.KEY_A: (2, 0), E.KEY_S: (2, 1), E.KEY_D: (2, 2), E.KEY_F: (2, 3),
        E.KEY_G: (2, 4), E.KEY_H: (2, 5), E.KEY_J: (2, 6), E.KEY_K: (2, 7),

        E.KEY_Z: (3, 0), E.KEY_X: (3, 1), E.KEY_C: (3, 2), E.KEY_V: (3, 3),
        E.KEY_B: (3, 4), E.KEY_N: (3, 5), E.KEY_M: (3, 6), E.KEY_COMMA: (3, 7),
    }
    KEY_CH1 = E.KEY_9
    KEY_CH2 = E.KEY_O
    KEY_CH3 = E.KEY_L
    KEY_CH4 = E.KEY_DOT

    KEY_CYCLE = E.KEY_TAB
    KEY_LEFT = E.KEY_LEFTSHIFT
    KEY_RIGHT = E.KEY_RIGHTSHIFT
    KEY_OUT = E.KEY_LEFTALT
    KEY_IN = E.KEY_RIGHTALT

    KEY_RED = E.KEY_0
    KEY_YELLOW = E.KEY_P
    KEY_GREEN = E.KEY_MINUS
    KEY_CYAN = E.KEY_LEFTBRACE
    KEY_BLUE = E.KEY_EQUAL
    KEY_MAGENTA = E.KEY_RIGHTBRACE
    KEY_WHITE = E.KEY_SEMICOLON
    KEY_BLACK = E.KEY_APOSTROPHE
    KEY_MORE = E.KEY_BACKSLASH
    KEY_LESS = E.KEY_BACKSPACE

    # Unused:
    #E.KEY_SLASH
    #E.KEY_ENTER
    
    def __init__(self, mainui): 
        self.pattern = None
        self.mainui = mainui

        self.zoom_offset = 0
        self.zoom_level = Pattern.SEQ_LEN 
        self.channel_offset = 0
        self.channel_active = 0

        self.grid_rows = []
        self.grid_marks = []
        self.grid_texts = []
        self.grid_descs = []
        self.grid_mutes = []
        self.grid_clears= []

        def grid_clear_click(btn, user_data):
            if self.pattern is not None:
                self.pattern.data[user_data] = [0] * Pattern.SEQ_LEN
                self.grid_texts[user_data].update()

        for i in range(Pattern.CHANNELS):
            mark = urwid.Text(">")
            #txt = urwid.Text("#" * Pattern.SEQ_LEN)
            txt = PatternText()
            desc = urwid.Text("-")
            mute = urwid.CheckBox("", state=False)
            clear = urwid.Button("", grid_clear_click, user_data=i)
            row = urwid.Columns([(2, mark), (Pattern.SEQ_LEN, txt), (6, clear), (4, mute), desc])
            self.grid_rows.append(row)
            self.grid_marks.append(mark)
            self.grid_texts.append(txt)
            self.grid_descs.append(desc)
            self.grid_mutes.append(mute)
            self.grid_clears.append(clear)

        self.timing_row = urwid.Text('')
        self.grid = urwid.Pile([('pack', r) for r in  (self.grid_rows + [self.timing_row])])

        self.title = urwid.Edit(caption="Title:", edit_text="Pattern 1")
        
        self.speed_btns = []
        for sp in [1, 2, 4]:
            urwid.RadioButton(self.speed_btns, "%dx Speed" % sp, user_data=sp)
        self.details = urwid.Pile([self.title] + self.speed_btns)
        self.content = urwid.Columns([('weight', 3, self.grid), ('weight', 1, self.details)])
        self.base = urwid.AttrMap(urwid.LineBox(self.content), 'inactive_window')

        self.update_marks(time=(0,0))

    def update_marks(self, time=None):
        # Update timing & channel marks
        for i in range(Pattern.CHANNELS):
            if i == self.channel_active:
                m = ">"
            elif self.channel_offset <= i < (self.channel_offset + 4):
                m = ":"
            else:
                m = " "
            self.grid_marks[i].set_text(m)

        if time is not None:
            beat, tick = time
            self.time_idx = beat * 8 + (tick / 30)

        time_text = []
        for i in range(Pattern.SEQ_LEN):
            if i == self.time_idx:
                time_text.append("^")
            elif i % 8 == 0:
                time_text.append("|")
            elif i % 4 == 0:
                time_text.append("-")
            elif self.zoom_offset <= i < (self.zoom_level + self.zoom_offset):
                time_text.append("_")
            else:
                time_text.append(".")
        
        self.timing_row.set_text("  " + ''.join(time_text))

    def load_pattern(self, pattern=None):
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
            self.grid_texts[i].update(pattern=pattern, index=i)
            self.grid_descs[i].set_text(pattern.get_channel_description(i))
            self.grid_mutes[i].set_state(pattern.channels_muted[i], do_callback=False)
        self.title.set_edit_text(pattern.title)
        for sbtn in self.speed_btns:
            if False and pattern.speed == sbtn.user_data:
                sbtn.toggle_state()
                break

    def keyboard_event(self, event, mode=False):
        kid, ev, pressed = event
        if mode:
			if ev.value == 0: #Key Up
				if ev.code in self.KEYS_GRID:
					ch, bt = self.KEYS_GRID[ev.code]
					channel = self.channel_offset + ch
					beat = (self.zoom_level / 8) * bt
					self.pattern.tap(channel, beat, width=self.zoom_level/8, keyboard=True)
					self.load_pattern()
				elif ev.code == self.KEY_CYCLE:
					self.channel_offset += 4
					if self.channel_offset >= Pattern.CHANNELS:
						self.channel_offset = 0

class PatternButton(urwid.WidgetWrap):
    def __init__(self, pattern, mainui):
        self.pattern = pattern
        self.mainui = mainui

        self.content = urwid.Text('') 
        self.box = urwid.LineBox(urwid.Padding(self.content))
        super(PatternButton, self).__init__(urwid.AttrMap(self.box, 'inactive_btn'))
        self.refresh()

    def refresh(self):
        if self.pattern is None:
            self.content.set_text("New Pattern")
            self._w.set_attr_map({None: 'new_btn'})
            self.keybinding = (E.KEY_INSERT, None)
        else:
            self.content.set_text(self.pattern.title)
            if self.pattern.active:
                self._w.set_attr_map({None: 'active_btn'})
            else:
                self._w.set_attr_map({None: 'inactive_btn'})
            self.keybinding = self.pattern.keybinding

    def mouse_event(self, size, event, button, col, row, focus):
        if event == "mouse press":
            if button == 1: # Left button
                self.press()
                return True
            elif button == 2: # Middle button
                self.edit()
                return True
        elif event == "ctrl mouse press":
            self.edit()
            return True
        return False

    def press(self):
        if self.pattern is not None:
            self.pattern.toggle()
        else:
            pass
        self.refresh()

    def edit(self):
        self.mainui.seqgrid.load_pattern(self.pattern)
        

class PatternGrid(object):
    HOTKEYS = [E.KEY_F1, E.KEY_F2, E.KEY_F3, E.KEY_F4,
               E.KEY_F5, E.KEY_F6, E.KEY_F7, E.KEY_F8,
               E.KEY_F9, E.KEY_F10, E.KEY_F11, E.KEY_F12,
               E.KEY_HOME, E.KEY_END, E.KEY_INSERT, E.KEY_DELETE ]

    HOTKEY_NAMES = ["F1", "F2", "F3", "F4",
                    "F5", "F6", "F7", "F8",
                    "F9", "F10", "F11", "F12",
                    "Hom", "End", "Ins", "Del" ]

    HOTKEY_DICT = dict(zip(HOTKEYS, HOTKEY_NAMES))

    def __init__(self, patterns, mainui):
        self.patterns = patterns
        self.mainui = mainui

        self.new_pattern = urwid.LineBox(urwid.Padding(urwid.Text("New")))
        self.content = urwid.GridFlow([], 16, 1, 1, 'center')
        self.base = urwid.AttrMap(urwid.LineBox(urwid.Filler(self.content)), 'inactive_window')

        self.rebuild_buttons()
    
    def make_button(self, pattern):
        #box = urwid.LineBox(urwid.Padding(urwid.Text(pattern.title)))
        #return urwid.AttrMap(box, 'active_btn' if pattern.active else 'inactive_btn')
        return PatternButton(pattern, self.mainui)

    def make_new_button(self):
        #box = urwid.LineBox(urwid.Padding(urwid.Text("New")))
        #return urwid.AttrMap(box, 'new_btn')
        return PatternButton(None, self.mainui)

    def rebuild_buttons(self):
        btns = []
        for pattern in self.patterns:
            btns.append((self.make_button(pattern), self.content.options()))
        btns.append((self.make_new_button(), self.content.options()))
        self.content.contents = btns

    def keyboard_event(self, event, mode=False):
        kid, ev, pressed = event
        if mode:
            if ev.value == 0: #Key Up
                pass

class SettingsBox(object):
    def __init__(self, mainui):
        self.mainui = mainui
        self.content = urwid.Pile([])
        self.base = urwid.AttrMap(urwid.LineBox(self.content), 'inactive_window')
    def keyboard_event(self, event, mode=False):
        kid, ev, pressed = event

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
        ('status_grabbed', '', '', '', '#FFF', '#f00'),
        ('active_btn', '', '', '', '#03f', '#fff'),
        ('inactive_btn', '', '', '', '#333', '#fff'),
        ('new_btn', '', '', '', '#888', '#fff'),
        ('active_window', '', '', '', '#03f', '#fff'),
        ('inactive_window', '', '', '', '#333', '#fff'),
    ]

    KEY_MODE = E.KEY_CAPSLOCK
    KEY_GRAB = E.KEY_ESC
    MODE_PAT = 0
    MODE_SEQ = 1

    def __init__(self, keyboards, device_manager):
        self.tb = Timebase()
        self.keyboards = keyboards
        #self.effects_runner = effects_runner
        self.device_manager = device_manager
        self.running = True

        self.mode = self.MODE_PAT
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

        self.patterns = [Pattern.new_template(self.device_manager) for i in range(10)]
        self.patterns[0].channels[0] = StrobeChannel(self.device_manager.devices[0], RGBA["red"])

        self.seqgrid = SequencingGrid(self)
        self.seqgrid.load_pattern(self.patterns[0])
        self.patgrid = PatternGrid(self.patterns, self)
        self.settings = SettingsBox(self)
        
        self.toggle_mode(new_mode=self.MODE_PAT)

        self.vbody = urwid.Pile([('pack', self.seqgrid.base), self.patgrid.base])
        self.body = urwid.Columns([('weight', 5, self.vbody), ('weight', 1, self.settings.base)])

        self.loop.widget.original_widget = urwid.Frame(body=self.body, header=self.header, footer=self.footer)

        self.status = urwid.Text(('status', 'CursedLight - Debug'), align='left')
        self.keyboard_status = urwid.Text(('status', 'Keyboard Free'), align='center')
        self.device_status = urwid.Text(('status', 'Devices'), align='right')
        self.bpm = urwid.Text("", align='left')
        self.ticker = urwid.Text("", align='right')

        self.header.contents.append((self.status, self.header.options()))
        self.header.contents.append((self.keyboard_status, self.header.options()))
        self.header.contents.append((self.device_status, self.header.options()))
        self.footer.contents.append((self.bpm, self.footer.options()))
        self.footer.contents.append((self.ticker, self.footer.options()))
#self.setup_devices()

        self.keypress_master = {
            #E.KEY_E: lambda ev: self.tb.quantize(),
            #E.KEY_G: lambda ev: self.tb.multiply(2),
            #E.KEY_H: lambda ev: self.tb.multiply(0.5),
            E.KEY_DELETE: lambda ev: self.stop(),
            #E.KEY_R: lambda ev: self.tb.sync(ev.timestamp()),
            #E.KEY_T: lambda ev: self.tb.tap(ev.timestamp()),
            #E.KEY_F: lambda ev: self.tb.nudge(1),
            #E.KEY_D: lambda ev: self.tb.nudge(-1),
        }

        global debug
        debug = lambda s: self.debug(s)

    def debug(self, s):
        self.device_status.set_text("Debug: %s" % s)

    def toggle_mode(self, new_mode=None):
        if new_mode is None:
            self.mode = self.MODE_PAT if self.mode == self.MODE_SEQ else self.MODE_SEQ
        else:
            self.mode = new_mode

        if self.mode == self.MODE_SEQ:
            self.seqgrid.base.set_attr_map({None: 'active_window'})
            self.patgrid.base.set_attr_map({None: 'inactive_window'})
            self.settings.base.set_attr_map({None: 'inactive_window'})
        elif self.mode == self.MODE_PAT:
            self.seqgrid.base.set_attr_map({None: 'inactive_window'})
            self.patgrid.base.set_attr_map({None: 'active_window'})
            self.settings.base.set_attr_map({None: 'inactive_window'})


    def master_kbd_handler(self, event):
        kid, ev, pressed = event
        if ev.value == 1: #T down
            if ev.code in self.keypress_master:
                self.keypress_master[ev.code](ev)
            if ev.code == self.KEY_MODE:
                self.toggle_mode()
        if ev.code == self.KEY_GRAB:
            if self.keyboards.kbds[0].grab:
                self.keyboard_status.set_text(("status_grabbed", "Keyboard Grabbed"))
            else:
                self.keyboard_status.set_text(("status", "Keyboard Free"))
        
        self.patgrid.keyboard_event(event, mode=self.mode==self.MODE_PAT)
        self.seqgrid.keyboard_event(event, mode=self.mode==self.MODE_SEQ)
        self.settings.keyboard_event(event, mode=False)


    def setup_devices(self):
        pass

    #def setup_devices(self):
    #    self.dguis = {}
    #    def kbd_handler(dgui, devs): return lambda ev: self.can_device_kbd_handler(ev, dgui, devs)
    #    for devgroup, devs in CAN_DEVICE_GROUPS.items():
    #        dgui = CANDeviceGroupUI(devgroup)
    #        self.body.contents.append((dgui.base, self.body.options()))
    #        dgui.dev_group.set_text("{} ({})".format(devgroup, len(devs)))
    #        self.dguis[devgroup] = dgui
    #        self.kbd_event_handlers[KEYBOARD_MAP[devgroup]].append(
    #                kbd_handler(dgui, devs)
    #        )
    #    self.device_status.set_text("Devices: {} CAN".format(len(self.effects_runner.canbus.addresses)-1))
    #    if IRON_CURTAIN_ENABLED:
    #        icui = IronCurtainUI(lambda sc: self.iron_curtain_ui_handler(sc))
    #        self.body.contents.append((icui.base, self.body.options()))
    #        self.dguis[IRON_CURTAIN] = icui
    #        self.kbd_event_handlers[KEYBOARD_MAP[IRON_CURTAIN]].append(
    #            (lambda ui: lambda ev: self.iron_curtain_kbd_handler(ev, ui))(icui)
    #        )
    #        self.device_status.set_text("Devices: {} CAN + Iron Curtain".format(len(self.effects_runner.canbus.addresses)-1))

    def loop_forever(self):
        self.loop.run()

    def idle_loop(self):
        tick = self.tb.tick()
        for pattern in self.patterns:
            pattern.tick(tick)
        self.seqgrid.update_marks(time=tick)
        self.ticker.set_text([('bpm_text', 'Tick: '), ('bpm', '{0}.{1:03d}'.format(*tick))])
        self.bpm.set_text([('bpm_text', 'BPM: '), ('bpm', '{: <6.01f}'.format(self.tb.bpm))])
        if tick[1] < 10:
            self.keyboards.set_all_leds(caps=tick[0] == 0)

        self.device_manager.tick(tick)

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
        #bus = FakeCanBus("/dev/ttyUSB0", 115200)
#bus = CanBus("/dev/ttyUSB0", 115200)
        led_strip = FakeSingleBespeckleDevice("/dev/ttyUSB0", 115200)
        device_manager = DeviceManager([led_strip])
        #effects_runner = EffectsRunner(bus)
        #[effects_runner.add_device(*dev) for dev in CAN_DEVICES.items()]
        ui = CursedLightUI(keyboards, device_manager)
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
