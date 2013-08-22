import urwid
import colorsys
import logging

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

HSVA = {
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
    "red": [0xff, 0x00, 0x00, 0xff],
}

for name, hsva in HSVA.items():
    RGBA[name] = hsva_to_rgba(hsva)

class EffectUI(object):
    def __init__(self, effect):
        self.effect = effect
        self.text = urwid.Text(str(self.effect))
        self.base = self.text #urwid.Filler(self.text)
        self.update()

    def update(self):
        self.text.set_text(str(self.effect))


class Effect(object):
    """
    An Effect represents a particular state mirrored across (possibly several) light strips.

    It is written with the intent of the same data being sent to every device, although 
    this is not strictly required.
    """
    effect_id = 0x00
    effect_name = "(Generic Effect)"
    ui_class = EffectUI
    def __init__(self, canbus, device_ids, unique_id, *args, **kwargs):
        """
        Initialize the effect:
        - Keep track of `canbus`, `device_ids`, etc.
        - Set start/stopped state
        - Call `self.init(...)` *** Override `.init(self, *args, **kwargs)` not `.__init__`!
        - Call `self.start()`
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

        self.start()

        self.started = True

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
        self.msg([0] * 6)
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

class SolidColorEffect(Effect):
    effect_id = 0x00
    effect_name = "Solid Color"
    ui_class = ColorEffectUI
    def init(self, color_hsva):
        self.color_hsva = color_hsva
        self.color_rgba = hsva_to_rgba(self.color_hsva)

    def start(self):
        self.msg(self.color_hsva)

class PulseColorEffect(Effect):
    effect_id = 0x08 #FIXME XXX
    effect_name = "Color Pulse"
    ui_class = ColorEffectUI
    def init(self, color_hsva):
        self.color_hsva = color_hsva
        self.color_rgba = hsva_to_rgba(self.color_hsva)
    
    def start(self):
        self.tick((0, 0))

    def tick(self, t):
        # Send a new pulse every measure
        if t == (0, 0):
            self._msg_all([self.effect_id, self.unique_id] + self.color_hsva)
        

class FlashRainbowEffect(Effect):
    effect_id = 0x00
    effect_name = "Flash Rainbow"
    ui_class = ColorEffectUI
    colors = ["red", "orange", "yellow", "green", "blue", "purple"]
    def init(self):
        self.i = 0

    def start(self):
        self.tick((0,0))

    def tick(self, t):
        tick, frac = t
        if frac == 0:
            self._msg_all([self.effect_id, self.unique_id] + self.color_hsva)
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
        self.msg([self.l_period, self.t_period, 0, 0, 0, 0])

