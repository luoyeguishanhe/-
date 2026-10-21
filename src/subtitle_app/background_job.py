"""Small cancellable background job; UI receives events on its own thread."""

from queue import Empty, Queue
from threading import Event, Thread


class Cancelled(Exception):
    pass


class BackgroundJob:
    def __init__(self, operation):
        self.events = Queue()
        self.cancel_event = Event()
        self.thread = Thread(target=self._run, args=(operation,), daemon=True)

    def _run(self, operation):
        try:
            result = operation(self.cancel_event, lambda kind, value: self.events.put((kind, value)))
            self.events.put(("complete", result))
        except Cancelled:
            self.events.put(("cancelled", None))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def start(self):
        self.thread.start()

    def cancel(self):
        self.cancel_event.set()

    def next_event(self):
        try:
            return self.events.get_nowait()
        except Empty:
            return None
