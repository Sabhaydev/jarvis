"""A tiny event bus: everything Jarvis does is published here and streamed to the web dashboard."""

import itertools
import queue
import threading
import time


class Bus:
    def __init__(self, keep=400):
        self.keep = keep
        self.history = []
        self.subscribers = []
        self.state = {
            "status": "starting",   # starting | idle | listening | hearing | thinking | speaking | paused
            "brain": "Built-in command engine",
            "stt": "-",
            "tts": "-",
            "awake": False,
        }
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def emit(self, kind, **data):
        event = {"id": next(self._ids), "t": time.time(), "kind": kind, **data}
        with self._lock:
            self.history.append(event)
            del self.history[:-self.keep]
            subs = list(self.subscribers)
        for q in subs:
            q.put(event)
        return event

    def set_state(self, **changes):
        changed = {k: v for k, v in changes.items() if self.state.get(k) != v}
        if changed:
            self.state.update(changed)
            self.emit("state", **self.state)

    def log(self, text, level="info"):
        print(f"[{level}] {text}")
        self.emit("log", level=level, text=text)

    def subscribe(self):
        q = queue.Queue()
        with self._lock:
            self.subscribers.append(q)
            snapshot = list(self.history)
        return q, snapshot

    def unsubscribe(self, q):
        with self._lock:
            if q in self.subscribers:
                self.subscribers.remove(q)


bus = Bus()
