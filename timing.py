import logging
import time

from config import *

logger = logging.getLogger(__name__)

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
