"""Bounded execution continuity and an opt-in Windows idle-sleep hold."""

import os
import threading
import time

from tools.qualification.resources import ResourceLimit


class ExecutionGuard:
    def __init__(self, *, keep_awake=False, gap_seconds=30, interval=1,
                 clock=time.time, set_state=None):
        self.keep_awake = keep_awake
        self.gap_seconds, self.interval, self.clock = gap_seconds, interval, clock
        self.set_state = set_state
        self.stop = threading.Event()
        self.interrupted = threading.Event()
        self.maximum_gap = 0
        self.previous_state = None

    def __enter__(self):
        if self.keep_awake:
            if self.set_state is None:
                if os.name != 'nt':
                    raise ValueError('--keep-awake is supported only on Windows')
                import ctypes
                from ctypes import wintypes
                self.set_state = ctypes.WinDLL('kernel32', use_last_error=True).SetThreadExecutionState
                self.set_state.argtypes = [wintypes.DWORD]
                self.set_state.restype = wintypes.DWORD
            self.previous_state = self.set_state(0x80000001)  # Continuous + system; display unchanged.
            if not self.previous_state:
                raise RuntimeError('Could not establish the requested idle-sleep hold')
        self.previous_tick = self.clock()
        self.thread = threading.Thread(target=self._watch, daemon=True)
        try:
            self.thread.start()
        except BaseException:
            self._release()
            raise
        return self

    def observe(self):
        now = self.clock()
        elapsed = now - self.previous_tick
        self.previous_tick = now
        self.maximum_gap = max(self.maximum_gap, abs(elapsed))
        if elapsed > self.gap_seconds or elapsed < -self.gap_seconds:
            self.interrupted.set()

    def _watch(self):
        while not self.stop.wait(self.interval):
            self.observe()

    def check(self):
        if self.interrupted.is_set():
            raise ResourceLimit('Execution continuity lost: scheduling or clock gap exceeded limit')

    def _release(self):
        if self.previous_state is not None:
            previous, self.previous_state = self.previous_state, None
            if not self.set_state(previous | 0x80000000):
                raise RuntimeError('Could not release the idle-sleep hold')

    def __exit__(self, exc_type, exc, traceback):
        self.stop.set()
        self.thread.join(timeout=5)
        self.observe()
        self._release()
        if exc_type is None:
            self.check()

    def evidence(self):
        return {'idle_sleep_hold': self.keep_awake, 'maximum_scheduling_gap_seconds': self.maximum_gap,
                'gap_limit_seconds': self.gap_seconds, 'continuity_lost': self.interrupted.is_set(),
                'scope': 'Idle sleep only; explicit sleep and lid-close remain possible'}
