import colorsys
import logging
import urwid

from effects import *
from timing import Timebase

logger = logging.getLogger(__name__)

class ChannelUI(object):
    def __init__(self, effect):
        self.effect = effect
        self.text = urwid.Text(str(self.effect))
        self.base = self.text #urwid.Filler(self.text)
        self.update()

    def update(self):
        self.text.set_text("--100%-- {}".format(str(self.effect)))

class Channel(object):
    ui_class = ChannelUI

    def __init__(self, device, *args, **kwargs):
        self.device = device
        self.init(*args, **kwargs)
        self.ui = self.ui_class(self)

    def init(self, *args, **kwargs):
        pass
    
    def start(self):
        pass

    def tick(self, time, value):
        beat, tick = time

    def stop(self):
        pass

    def keyboard_event(self, event):
        kid, ev, pressed = event

    def update(self):
        if self.ui is not None:
            self.ui.update()

    def __str__(self):
        return "Channel"

class StrobeChannelUI(ChannelUI):
    def __init__(self, channel):
        self.channel = channel
        self.text = urwid.Text(str(self.channel))
        self.base = self.text #urwid.AttrMap(self.text, {})
        self.update()

    def update(self):
        termcolor, alpha, textcolor = rgba_to_termcolor(self.channel.color_rgba)
        if self.channel.last_on is None:
            termcolor = "#ccc"
        colorspec = urwid.AttrSpec(textcolor, termcolor, 256)
        self.text.set_text([(colorspec, '  {:.0%}  '.format(alpha)), (None, ' ' + str(self.channel))])


class StrobeChannel(Channel):
    bespeckle_effect_class = 0x10
    ui_class = StrobeChannelUI

    def init(self, color_rgba=RGBA["white"], width=1):
        self.color_rgba = color_rgba
        self.bespeckle_id = None
        self.last_on = None
        self.width = width

    def start(self):
        data = []
        self.bespeckle_id = self.device.bespeckle_add_effect(self.bespeckle_effect_class, data)

    def tick(self, time, value):
        beat, tick = time
        if self.bespeckle_id is None:
            return 
        if value:
            if self.last_on is None:
                self.device.bespeckle_msg_effect(self.bespeckle_id, self.color_rgba + [0x0, 0x03])
            self.last_on = time
        elif self.last_on is not None and Timebase.difference(self.last_on, time) > self.width:
            self.device.bespeckle_msg_effect(self.bespeckle_id, RGBA["clear"] + [0x0, 0x82])
            self.last_on = None

    def stop(self):
        if self.bespeckle_id is not None:
            self.device.bespeckle_pop_effect(self.bespeckle_id)
            self.bespeckle_id = None

