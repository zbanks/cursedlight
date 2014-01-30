import colorsys
import logging
import urwid

logging.basicConfig(filename="/tmp/cl.log", level=logging.DEBUG)
logger = logging.getLogger(__name__)

def rgba_to_hsva(rgba):
    r,g,b,a = rgba
    h,s,v = colorsys.rgb_to_hsv(r/255.0, g/255.0, b/255.0)
    h *= 255
    s *= 255
    v *= 255
    return map(int, [h, s, v, a])

def hsva_to_rgba(hsva):
    h,s,v,a = hsva
    r,g,b = colorsys.hsv_to_rgb(h/255.0, s/255.0, v/255.0)
    r *= 255
    g *= 255
    b *= 255
    return map(int, [r,g,b,a])

def contrast_bw(rgba): 
    r,g,b,a = rgba
    if r+g+b > 150:
        return '#000'
    return '#fff'

def rgba_to_termcolor(rgba):
    r,g,b,a = rgba
    def h(x): return hex((x)/16)[-1]
    return ("#" + "".join(map(h, [r,g,b])), a / 255.0, contrast_bw(rgba))

def rgb_add(base, delta, scale=8, neg=False):
    clip = lambda x: min(max(x, 0), 0xff)
    if neg:
        return [clip(b - (d / scale)) for b, d in zip(base[0:3], delta[0:3])] + [base[3]]
    return [clip(b + (d / scale)) for b, d in zip(base[0:3], delta[0:3])] + [base[3]]

HSVA = {
    "clear":     [0x00, 0x0, 0x0, 0x0],
    "red":     [0x00, 0xff, 0xff, 0xff],
    "orange":  [254/12, 0xff, 0xff, 0xff],
    "yellow":  [254/6, 0xff, 0xff, 0xff],
    "green":   [254/3, 0xff, 0xff, 0xff],
    "cyan":    [130, 0xff, 0xff, 0xff],
    "blue":    [170, 0xff, 0xff, 0xff],
    "purple": [200, 0xff, 0xff, 0xff],
    "white": [0x00, 0x00, 0xff, 0xff],
    "black": [0x00, 0x00, 0x00, 0xff],
}


RGBA = {
    "clear":     [0x00, 0x0, 0x0, 0x0],
    "red": [0xff, 0x00, 0x00, 0xff],
    "green": [0x00, 0xff, 0x00, 0xff],
    "blue": [0x00, 0x00, 0xff, 0xff],
    "yellow": [0xff, 0xff, 0x00, 0xff],
    "cyan": [0x00, 0xff, 0xff, 0xff],
    "magenta": [0xff, 0x00, 0xff, 0xff],
    "white": [0xff, 0xff, 0xff, 0xff],
    "black": [0x00, 0x00, 0x00, 0xff],
}

for name, hsva in HSVA.items():
    if name not in RGBA:
        RGBA[name] = hsva_to_rgba(hsva)

class EffectUI(object):
    def __init__(self, effect):
        self.effect = effect
        self.text = urwid.Text(str(self.effect))
        self.base = self.text #urwid.Filler(self.text)
        self.update()

    def update(self):
        self.text.set_text("--100%-- {}".format(str(self.effect)))


class Effect(object):
    """
    An Effect represents a particular state mirrored across (possibly several) light strips.

    It is written with the intent of the same data being sent to every device, although 
    this is not strictly required.
    """
    effect_id = 0x00
    effect_name = "(Generic Effect)"
    ui_class = EffectUI
    CMD_TICK = 0x80
    CMD_RESET = 0xFF
    CMD_MSG = 0x81
    def __init__(self, canbus, device_ids, unique_id, *args, **kwargs):
        """
        Initialize the effect:
        - Keep track of `canbus`, `device_ids`, etc.
        - Set start/stopped state
        - Call `self.init(...)` *** Override `.init(self, *args, **kwargs)` not `.__init__`!
        #- Call `self.start()`
        - Create a UI element to represent it

        For the sake of my sanity: the unique_id is the same for each device. 
        """
        self.canbus = canbus
        self.device_ids = device_ids
        self.unique_id = unique_id

        self.started = False
        self.stopped = False

        self.init(*args, **kwargs)
        self.ui = self.ui_class(self)

        #self.start()
        #self.started = True
        self.started = False

    def _msg_device(self, device_id, data):
        # Send a message to a single device
        self.canbus.can_packet(device_id, data)

    def _msg_all(self, data):
        # Send a message to every device
        for did in self.device_ids:
            self._msg_device(did, data)

    def msg(self, data):
        # Send a message with the approprate prefix. Just specify the 6 bytes of generic data
        if self.stopped:
            return
        if self.started:
            self._msg_all([self.canbus.CMD_MSG, self.unique_id] + data)
        else:
            self._msg_all([self.effect_id, self.unique_id] + data)

    def start(self):
        # Override this method!
        self.msg([0] * 6)
        self.ui.update()

    def init(self, *args, **kwargs):
        # Override this method!
        self.ui.update()

    def tick(self, t):
        # Override this method!
        # If you need to do anything per tick
        #beat, frac = t
        #self.ui.update()
        pass

    def stop(self):
        # Override this method!
        # This isn't actually standardized...
#self.msg([0] * 6)
        self._msg_all([self.canbus.CMD_STOP, self.unique_id])
        self.stopped = True
        self.ui.update()

    def __str__(self):
        # Override this method if you want
        return self.effect_name

class ColorEffectUI(EffectUI):
    def __init__(self, effect):
        self.effect = effect
        self.text = urwid.Text(str(self.effect))
        self.base = self.text #urwid.AttrMap(self.text, {})
        self.update()

    def update(self):
        termcolor, alpha, textcolor = rgba_to_termcolor(self.effect.color_rgba)
        colorspec = urwid.AttrSpec(textcolor, termcolor, 256)
        self.text.set_text([(colorspec, '  {:.0%}  '.format(alpha)), (None, ' ' + str(self.effect))])

class StrobeColorEffectUI(EffectUI):
    def __init__(self, effect):
        self.effect = effect
        self.text = urwid.Text(str(self.effect))
        self.base = self.text #urwid.AttrMap(self.text, {})
        self.update()

    def update(self):
        termcolor, alpha, textcolor = rgba_to_termcolor(self.effect.color_rgba)
        if self.effect.clear:
            termcolor = "#ccc"
        colorspec = urwid.AttrSpec(textcolor, termcolor, 256)
        self.text.set_text([(colorspec, '  {:.0%}  '.format(alpha)), (None, ' ' + str(self.effect) + str(self.effect.rate))])
        
class SolidColorEffect(Effect):
    effect_id = 0x10
    effect_name = "Solid Color"
    ui_class = ColorEffectUI
    def init(self, color_rgba):
#self.color_hsva = color_hsva
        self.color_rgba = color_rgba

    def start(self):
        self.msg(self.color_rgba)

class StrobeColorEffect(Effect):
    effect_id = 0x10
    effect_name = "Solid Color"
    strlen = 10
    ui_class = StrobeColorEffectUI
    def init(self, color_rgba, rate=3):
#self.color_hsva = color_hsva
        self.color_rgba = color_rgba
        self.rate = rate
        self.clear = True
        self.retry = 0

    def start(self):
        self.msg(RGBA["clear"])

    def tick(self, t):
        # Send a new pulse every measure
        if t[1] < self.strlen:
            if t[0] == 0:
                if self.rate <= 3:
                    self.msg(self.color_rgba)
                    self.clear = False
                    self.ui.update()
            elif t[0] == 2:
                if self.rate <= 2:
                    self.msg(self.color_rgba)
                    self.clear = False
                    self.ui.update()
            else:
                if self.rate <= 1:
                    self.msg(self.color_rgba)
                    self.clear = False
                    self.ui.update()
        elif self.retry or  t[1] > self.strlen and not self.clear:
            if self.retry:
                self.retry -= 1
            else:
                self.retry = 5
            self.msg(RGBA["clear"])
            self.clear = True
            self.ui.update()

class PulseColorEffect(Effect):
    effect_id = 0x14
    effect_name = "Pulse"
    ui_class = ColorEffectUI
    def init(self, color_rgba, rate=0x0A):
        self.color_rgba = color_rgba
        self.rate = 0x01 #rate
    
    def start(self):
        # [color, 0, rate]
        # rate & 0x7 is speed, rate & 0x8 is direction
        self.msg(self.color_rgba + [0, self.rate])

    def tick(self, t):
        # Send a new pulse every measure
        if t == (0, 0):
            # Thickness
            self.msg([1])

class SwipeColorEffect(Effect):
    effect_id = 0x16
    effect_name = "Swipe"
    ui_class = ColorEffectUI
    def init(self, color_rgba, rate=0x09):
        self.color_rgba = color_rgba
        self.rate = 0x00#rate
    
    def start(self):
        # [color, 0, rate]
        # rate & 0x7 is speed, rate & 0x8 is direction
        self.msg(self.color_rgba + [0, self.rate])

    def tick(self, t):
        # Send a new pulse every measure
        if t == (0, 0):
            # Thickness
            self.msg([4])

class FlashRainbowEffect(Effect):
    effect_id = 0x10
    effect_name = "Flash Rainbow"
    ui_class = ColorEffectUI
    colors = ["red", "orange", "yellow", "green", "blue", "purple"]
    def init(self):
        self.i = 0

    def start(self):
        self._msg_all([self.effect_id, self.unique_id] + self.color_rgba)
# self.tick((0,0))

    def tick(self, t):
        tick, frac = t
        if frac == 0:
            self._msg_all([self.CMD_MSG, self.unique_id] + self.color_rgba)
#self._msg_all([self.effect_id, self.unique_id] + self.color_hsva)
            self.ui.update()
            self.i = (self.i + 1) % len(self.colors)

    @property
    def color_hsva(self):
        return HSVA[self.colors[self.i]]

    @property
    def color_rgba(self):
        return RGBA[self.colors[self.i]]

class RainbowEffect(Effect):
    effect_id = 0x03
    effect_name = "Rainbow"
    def init(self, l_period=1, t_period=1):
        self.l_period = l_period
        self.t_period = t_period

    def start(self):
        # Format is [start, time, dist]
        self.msg([0, self.t_period, self.l_period, 0])

class FadeinEffect(Effect):
    effect_id = 0x12
    effect_name = "Fade to"
    ui_class = ColorEffectUI
    def init(self, color_rgba=RGBA['black'], rate=2):
        self.color_rgba = color_rgba
        self.rate = rate

    def start(self):
        self.msg(self.color_rgba + [0, self.rate])

class StrobeEffect(Effect):
    effect_id = 0x18
    effect_name = "Strobe"
    ui_class = ColorEffectUI
    def init(self, color_rgba=RGBA['white'], rate=3):
        self.color_rgba = color_rgba
        self.rate = rate

    def start(self):
        self.msg(self.color_rgba + self._get_rate())

    """
    Note: Msg 0xC0 to change color; Msg 0xC1 to change rate
    """

    def _get_rate(self):
        RATES = (
            [8, 255],
            [4, 255],
            [2, 255],
            [1, 255],
            [1, 128],
            [1, 64],
            [1, 32],
            [1, 16]
        )
        if self.rate >= len(RATES):
            return RATES[-1][::-1]
        if self.rate < 0:
            return [16, 255][::-1]
        return RATES[self.rate][::-1]

