from __future__ import annotations

import threading

from scripts.behavior_auxiliary import BoundedAuxiliaryRunner


def test_result_is_bound_to_each_input_and_runner_closes_cleanly() -> None:
    runner = BoundedAuxiliaryRunner(lambda value: f"result-{value}")
    first = runner.submit("first")
    assert first is not None
    assert runner.get(first, 1) == {
        "status": "ready",
        "result": "result-first",
        "error": None,
    }

    second = runner.submit("second")
    assert second is not None
    assert runner.get(second, 1)["result"] == "result-second"
    assert runner.get(first, 0)["result"] == "result-first"
    assert runner.close()
    assert not runner._thread.is_alive()
    assert runner.submit("closed") is None


def test_busy_submission_is_rejected_without_queueing() -> None:
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def blocked(value: str) -> str:
        calls.append(value)
        entered.set()
        release.wait()
        return value

    runner = BoundedAuxiliaryRunner(blocked)
    job = runner.submit("accepted")
    assert job is not None
    assert entered.wait(1)
    assert runner.submit("rejected") is None
    release.set()
    assert runner.get(job, 1)["status"] == "ready"
    assert calls == ["accepted"]
    assert runner.close()


def test_timeout_does_not_release_running_job_or_reuse_old_result() -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocked(value: str) -> str:
        entered.set()
        release.wait()
        return f"done-{value}"

    runner = BoundedAuxiliaryRunner(blocked)
    job = runner.submit("slow")
    assert job is not None
    assert entered.wait(1)
    assert runner.get(job, 0) == {
        "status": "timeout",
        "result": None,
        "error": "auxiliary job timed out",
    }
    assert runner.submit("must-not-run") is None
    assert not runner.close(timeout=0)

    release.set()
    assert runner.get(job, 1)["result"] == "done-slow"
    assert runner.close(timeout=1)


def test_exception_is_reported_and_never_reuses_previous_result() -> None:
    def sometimes_fails(value: str) -> str:
        if value == "bad":
            raise RuntimeError("synthetic failure")
        return value

    runner = BoundedAuxiliaryRunner(sometimes_fails)
    good = runner.submit("good")
    assert good is not None
    assert runner.get(good, 1)["result"] == "good"

    bad = runner.submit("bad")
    assert bad is not None
    assert runner.get(bad, 1) == {
        "status": "error",
        "result": None,
        "error": "RuntimeError: synthetic failure",
    }
    assert runner.close()
