"""Sequential translation worker for captions from an active audio capture."""

from dataclasses import replace
from queue import Empty, Full, Queue
from threading import Event, Thread

from .background_job import Cancelled
from .subtitles import SubtitleCue
from .translation import TranslationConfig, request_chat, translate_subtitles


class LiveSubtitleTranslator:
    def __init__(self, config: TranslationConfig, transport=request_chat) -> None:
        config.endpoint()
        self.config = replace(config, batch_size=1, timeout=min(config.timeout, 30))
        self.transport = transport
        self.events: Queue[tuple[str, object]] = Queue()
        self._pending: Queue[tuple[int, SubtitleCue]] = Queue(maxsize=2)
        self._cancel = Event()
        self._accepting = True
        self._failed = False
        self._sequence = 0
        self._thread = Thread(target=self._run, name="LiveSubtitleTranslator", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def submit(self, cue: SubtitleCue) -> bool:
        if not self._accepting or self._cancel.is_set() or self._failed:
            return False
        self._sequence += 1
        item = (self._sequence, cue)
        skipped = False
        while True:
            try:
                self._pending.put_nowait(item)
                break
            except Full:
                try:
                    self._pending.get_nowait()
                    skipped = True
                except Empty:
                    continue
        if skipped:
            self.events.put(("warning", "翻译速度跟不上语音，已跳过一条等待中的字幕"))
        self.events.put(("queued", (self._sequence, cue.text)))
        return True

    def finish(self) -> None:
        self._accepting = False

    def cancel(self) -> None:
        self._accepting = False
        self._cancel.set()

    @property
    def is_active(self) -> bool:
        return self._thread.is_alive()

    def next_event(self) -> tuple[str, object] | None:
        try:
            return self.events.get_nowait()
        except Empty:
            return None

    def _run(self) -> None:
        try:
            while not self._cancel.is_set():
                try:
                    number, cue = self._pending.get(timeout=0.1)
                except Empty:
                    if not self._accepting:
                        break
                    continue
                try:
                    def emit(kind, value):
                        if kind == "token_usage":
                            self.events.put(("token_usage", value))

                    result = translate_subtitles(
                        [cue], self.config, self._cancel, emit=emit, transport=self.transport)
                except Cancelled:
                    break
                except Exception as exc:
                    self._failed = True
                    self.events.put(("error", str(exc)))
                    break
                self.events.put(("translated", (number, cue.text, result[0].text)))
        finally:
            self.events.put(("complete", None))
