import zmq, threading, time
import beat_event_pb2


class BeatBlaster(object):
    

    def __init__(self, audience = None):

        self.audience = audience

        self.ctx = zmq.Context()
        self.publisher = self.ctx.socket(zmq.PUB)

        if self.audience:
            self.publisher.connect(audience)
        else:
            self.publisher.connect("tcp://*:8001")

        time.sleep(1)

    def beat(self, beat):
        beat_event = beat_event_pb2.BeatEvent()
        beat_event.beat = beat
        beat_event.type = beat_event_pb2.BEAT
        beat_event.sub_beat = 0
        self.publisher.send_multipart(['b', beat_event.SerializeToString()], zmq.NOBLOCK )


    def sub_beat(self, beat, sub_beat):
        beat_event = beat_event_pb2.BeatEvent()
        beat_event.beat = beat
        beat_event.type = beat_event_pb2.SUB_BEAT
        beat_event.sub_beat = sub_beat
        self.publisher.send_multipart(['s', beat_event.SerializeToString()], zmq.NOBLOCK)


    def change_scene(self, scene_number = 0):
        beat_event = beat_event_pb2.BeatEvent()
        beat_event.type = beat_event_pb2.CHANGE_SCENE
        self.publisher.send_multipart(['c', beat_event.SerializeToString()], zmq.NOBLOCK)


    def set_color(self, r = 0, g = 0, b = 0):
        beat_event = beat_event_pb2.BeatEvent()
        beat_event.r = max(abs(r), 255)
        beat_event.g = max(abs(g), 255)
        beat_event.b = max(abs(b), 255)
        beat_event.type = beat_event_pb2.COLOR
        self.publisher.send_multipart(['C', beat_event.SerializeToString()], zmq.NOBLOCK)







if __name__ == '__main__':
    try:
        n = BeatBlaster("tcp://*:8001")
        n.set_color(255, 255, 255)
        while True:
            for bar in range(4):
                n.beat(bar) 
                for x in range(255):
                    n.sub_beat(bar, x) 
                    time.sleep(1./255)
            
            #time.sleep(.25)
    except KeyboardInterrupt:
        print "Exiting"


