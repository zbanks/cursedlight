HSVA = {
    "red": [0x00, 0xff, 0xff, 0xff],
}

RGBA = {
    "red": [0xff, 0x00, 0x00, 0xff],
}

class Effect(object):
    """
    An Effect represents a particular state mirrored across (possibly several) light strips.

    It is written with the intent of the same data being sent to every device, although 
    this is not strictly required.
    """
    effect_id = 0x00
    effect_name = "(Generic Effect)"
    def __init__(self, canbus, device_ids, unique_id, *args, **kwargs):
        """
        Initialize the effect:
        - Keep track of `canbus`, `device_ids`, etc.
        - Set start/stopped state
        - Call `self.init(...)` *** Override `.init(self, *args, **kwargs)` not `.__init__`!
        - Call `self.start()`

        For the sake of my sanity: the unique_id is the same for each device. 
        """
        self.canbus = canbus
        self.device_ids = device_ids
        self.unique_id = unique_id

        self.started = False
        self.stopped = False

        self.init(*args, **kwargs)

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

    def init(self, *args, **kwargs):
        # Override this method!
        pass

    def stop(self):
        # Override this method!
        # This isn't actually standardized...
        self.msg([0] * 6)
        self.stopped = True

    def __str__(self):
        # Override this method if you want
        return self.effect_name

class SolidColorEffect(Effect):
    effect_id = 0x00
    effect_name = "Solid Color"
    def init(self, color_hsva):
        self.color_hsva = color_hsva

    def start(self):
        self.msg(self.color_hsva + [0, 0])

class RainbowEffect(Effect):
    effect_id = 0x03
    effect_name = "Rainbow"
    def init(self, l_period=1, t_period=1):
        self.l_period = l_period
        self.t_period = t_period

    def start(self):
        self.msg([self.l_period, self.t_period, 0, 0, 0, 0])

