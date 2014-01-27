import Queue
import asyncore
import evdev
import logging
import threading

from config import *

logger = logging.getLogger(__name__)

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
                    if ev.code == evdev.ecodes.KEY_ESC:
                        if self.grab:
                            self.dev.ungrab()
                            self.grab = False
                        else:
                            self.dev.grab()
                            self.grab = True
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
        dograb = False
        for path in evdev.list_devices()[::-1]:
            kbd = AsyncRawKeyboard(path, len(self.kbds), self.events, grab=dograb)
            if not kbd.is_keyboard:
                continue
            dograb = True
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
