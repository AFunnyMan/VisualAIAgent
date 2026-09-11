"""Bounded single-thread runner for optional behavior-model work."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict


class AuxiliaryOutcome(TypedDict):
    status: Literal["ready", "timeout", "error"]
    result: Any | None
    error: str | None


@dataclass(slots=True)
class AuxiliaryJob:
    """Opaque handle whose completion state belongs to one submitted input."""

    _input: Any
    done: threading.Event = field(default_factory=threading.Event)
    result: Any | None = None
    error: str | None = None


class BoundedAuxiliaryRunner:
    """Run at most one optional job at a time without building a backlog."""

    def __init__(self, fn: Callable[[Any], Any]) -> None:
        self._fn = fn
        self._condition = threading.Condition()
        self._job: AuxiliaryJob | None = None
        self._accepting = True
        self._thread = threading.Thread(
            target=self._run,
            name="behavior-auxiliary",
            daemon=True,
        )
        self._thread.start()

    def submit(self, frame: Any) -> AuxiliaryJob | None:
        """Accept an input only when no earlier job is queued or running."""
        with self._condition:
            if not self._accepting or self._job is not None:
                return None
            job = AuxiliaryJob(_input=frame)
            self._job = job
            self._condition.notify()
            return job

    def get(self, job: AuxiliaryJob, timeout: float | None = None) -> AuxiliaryOutcome:
        """Wait boundedly for this job without consuming another job's result."""
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must not be negative")
        if not job.done.wait(timeout):
            return {"status": "timeout", "result": None, "error": "auxiliary job timed out"}
        if job.error is not None:
            return {"status": "error", "result": None, "error": job.error}
        return {"status": "ready", "result": job.result, "error": None}

    def close(self, timeout: float = 5.0) -> bool:
        """Stop accepting work and wait at most timeout seconds for the worker."""
        if timeout < 0:
            raise ValueError("timeout must not be negative")
        with self._condition:
            self._accepting = False
            self._condition.notify_all()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout)
        return not self._thread.is_alive()

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._job is None and self._accepting:
                    self._condition.wait()
                if self._job is None:
                    return
                job = self._job
            try:
                job.result = self._fn(job._input)
            except Exception as exc:
                job.error = f"{type(exc).__name__}: {exc}"
            finally:
                job._input = None
                with self._condition:
                    if self._job is job:
                        self._job = None
                    if not self._accepting:
                        self._condition.notify_all()
                job.done.set()
