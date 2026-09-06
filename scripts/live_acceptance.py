"""Opt-in real Agent + local video acceptance; never opens a physical camera."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from uuid import uuid4
from zoneinfo import ZoneInfo

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual_ai_agent.config import Config  # noqa: E402
from visual_ai_agent.runtime import ApplicationRuntime  # noqa: E402
from visual_ai_agent.vision import ReplaySource, VisionWorker, YoloOnnxDetector  # noqa: E402


class ControlledVideoSource:
    """Real video plus explicitly synthetic blank/fault modes for pipeline failure checks."""

    source_name = "replay"

    def __init__(self, path: Path):
        self.video = ReplaySource(path, loop=True, realtime=True)
        self.status = "stopped"
        self.last_error = None
        self.mode = "video"
        self._lock = RLock()

    def open(self):
        with self._lock:
            self.video.open()
            self.status = "running"

    def read(self):
        # Serializes OpenCV decode/release; only this test source adds mode switching.
        with self._lock:
            frame = self.video.read()
            if frame is None:
                self.status = self.video.status
                return None
            if self.mode == "fault":
                self.status = "disconnected"
                self.last_error = "Explicit injected replay fault"
                return None
            self.status = "running"
            self.last_error = None
            return frame * 0 if self.mode == "blank" else frame

    def close(self):
        with self._lock:
            self.video.close()
            self.status = "stopped"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def wait_for(predicate, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.2)
    raise AssertionError("Timed out waiting for the expected local state")


def assert_reply_facts(message, tool_name, tool_results, timezone):
    """Check answer anchors against actual tool returns, without prescribing exact prose."""
    categories = {
        "bottle": ("bottle", "瓶"),
        "cup": ("cup", "杯"),
        "cell phone": ("cell phone", "手机"),
    }
    regions = {"left": ("left", "左"), "center": ("center", "中"), "right": ("right", "右")}

    def category_present(category):
        require(
            any(word in message for word in categories[category]), "Answer omitted actual category"
        )

    def time_present(value):
        stamp = datetime.fromisoformat(value)
        times = (
            stamp.strftime("%H:%M:%S"),
            stamp.astimezone(ZoneInfo(timezone)).strftime("%H:%M:%S"),
        )
        require(any(value in message for value in times), "Answer omitted actual observation time")

    def region_present(value):
        require(any(word in message for word in regions[value]), "Answer omitted actual region")

    require(bool(tool_results), "Actual tool facts were not captured")
    facts = tool_results[-1]
    if tool_name == "find_object":
        if not facts.get("found"):
            require(
                any(word in message for word in ("未知", "没有", "未找到", "无记录", "未曾")),
                "Unknown history was not stated",
            )
        else:
            category_present(facts["category"])
            time_present(facts["last_seen_at"])
            require(facts["evidence_id"] in message, "Answer omitted actual evidence ID")
            for region in facts["regions"]:
                region_present(region)
    elif tool_name == "get_current_scene":
        if not facts["current"]:
            require(
                any(word in message for word in ("未知", "过期", "停止", "无有效", "没有有效")),
                "Invalid scene presented as current",
            )
        else:
            for detection in facts["observation"]["detections"]:
                category_present(detection["category"])
                region_present(detection["region"])
    elif tool_name == "search_events":
        require(bool(facts["events"]), "No event facts returned for video history")
        if not any(event["kind"] == "missing" for event in facts["events"]):
            require(
                not any(word in message for word in ("缺失事件", "未检测到事件", "消失事件"))
                or any(word in message for word in ("没有", "未找到", "未记录", "无此", "无缺失")),
                "Answer invented a missing event absent from the tool result",
            )
        for event in facts["events"]:
            category_present(event["category"])
            time_present(event["confirmed_at"])
            require(event["evidence_id"] in message, "Event answer omitted actual evidence ID")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--category", choices=["cell phone", "cup", "bottle"], default="bottle")
    parser.add_argument(
        "--idle-category", choices=["cell phone", "cup", "bottle"], default="cell phone"
    )
    parser.add_argument("--idle-seconds", type=float, default=600)
    args = parser.parse_args()
    if os.getenv("VAA_RUN_LIVE_API") != "1":
        parser.error("Set VAA_RUN_LIVE_API=1 to authorize bounded paid API calls")
    if not args.video.is_file() or args.idle_seconds < 1 or args.category == args.idle_category:
        parser.error("Provide an existing video, positive duration, and distinct idle category")
    original = Config.from_env()
    if not original.agent_connected:
        parser.error("Configure API URL, model and key in the ignored .env file")
    output = Path("harness/artifacts") / ("live-video-" + uuid4().hex[:8])
    output.mkdir(parents=True)
    config = replace(original, data_dir=output)
    summary = {
        "started_at": datetime.now(UTC).isoformat(),
        "source": "local video + synthetic blank/disconnect; see separate source/license record",
        "video_sha256": hashlib.sha256(args.video.read_bytes()).hexdigest(),
        "model": config.agent_model,
        "api_mode": config.api_mode,
        "platform": platform.platform(),
        "physical_camera": False,
        "steps": [],
        "user_runs": [],
        "http_audit": {"requests": 0, "text_only_requests": 0, "tool_counts": []},
        "passed": False,
    }
    runtime = None

    def save():
        (output / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def audit_client():
        async def audit(request):
            # Inspect outgoing payload structure only; never persist headers or message bodies.
            body = json.loads(request.content)
            require(str(request.url).startswith(config.api_base_url), "Unexpected API destination")
            require(len(body.get("tools", [])) == 7, "Unexpected business tool count")
            for message in body.get("messages", []):
                content = message.get("content")
                if isinstance(content, list):
                    require(
                        all(part.get("type") == "text" for part in content), "Non-text cloud input"
                    )
                else:
                    require(content is None or isinstance(content, str), "Non-text cloud input")
            summary["http_audit"]["requests"] += 1
            summary["http_audit"]["text_only_requests"] += 1
            summary["http_audit"]["tool_counts"].append(len(body["tools"]))

        require(
            config.api_mode == "chat_completions", "This payload audit expects Chat Completions"
        )
        runtime.agent._client._client.event_hooks["request"].append(audit)

    def step(name, **details):
        summary["steps"].append({"name": name, "at": datetime.now(UTC).isoformat(), **details})
        save()
        print(json.dumps({"step": name, **details}, ensure_ascii=False), flush=True)

    def query(message, expected_tool):
        captured = []
        original_tool = None
        if expected_tool in ("get_current_scene", "find_object", "search_events"):
            original_tool = getattr(runtime.memory, expected_tool)

            def capture(*args, **kwargs):
                result = original_tool(*args, **kwargs)
                if result.ok:
                    captured.append(result.model_dump(mode="json")["data"])
                return result

            setattr(runtime.memory, expected_tool, capture)
        try:
            result = runtime.submit_user(message).result(timeout=config.api_timeout_seconds + 10)
        finally:
            if original_tool is not None:
                setattr(runtime.memory, expected_tool, original_tool)
        summary["user_runs"].append(asdict(result))
        summary.setdefault("query_facts", []).append({"tool": expected_tool, "facts": captured})
        save()
        require(result.status == "completed", f"User request failed: {result.status}")
        require(
            any(call["tool"] == expected_tool and call["ok"] for call in result.tool_calls),
            f"Expected successful tool: {expected_tool}",
        )
        require(result.usage_complete and result.total_tokens is not None, "Missing real usage")
        if original_tool is not None:
            assert_reply_facts(result.message, expected_tool, captured, config.timezone)
        return result

    def events():
        now = datetime.now(UTC)
        return runtime.memory.search_events(args.category, now - timedelta(hours=1), now).data[
            "events"
        ]

    def notifications():
        return runtime.watches.list_notifications().data["notifications"]

    def watch_from(result):
        return next(c["watch_id"] for c in result.tool_calls if c["tool"] == "create_watch")

    def await_notification(watch_id):
        notice = wait_for(
            lambda: next((n for n in notifications() if n["watch_id"] == watch_id), None)
        )
        wait_for(lambda: runtime._jobs.unfinished_tasks == 0)
        require(notice["source"] == "agent", "Fallback does not pass real Agent acceptance")
        event = runtime.memory.get_event(notice["event_id"])
        require(event is not None and event.source == "replay", "Missing replay event")
        require(event.evidence_id is not None, "Missing event evidence")
        require(
            runtime.memory.evidence_path(event.evidence_id) is not None, "Missing evidence file"
        )
        runs = [
            r
            for r in runtime.memory.list_agent_runs()
            if r["request_id"] == f"event:{watch_id}:{event.event_id}"
        ]
        require(len(runs) == 1, "Missing or duplicated event Agent run")
        require(
            runs[0]["usage_complete"] and runs[0]["total_tokens"] is not None,
            "Incomplete event usage",
        )
        require(
            runs[0]["status"] == "completed" and 1 <= runs[0]["request_attempts"] <= 3,
            "Invalid event run status or count",
        )
        delay = (datetime.fromisoformat(notice["created_at"]) - event.confirmed_at).total_seconds()
        step("agent_notification", kind=event.kind, latency_seconds=delay, event_id=event.event_id)
        return notice

    def start_video(detector):
        source = ControlledVideoSource(args.video)
        worker = VisionWorker(detector, source, runtime.ingest)
        runtime._vision = worker
        worker.start()
        wait_for(lambda: runtime.memory.get_current_scene().data.get("current"))
        return source

    try:
        runtime = ApplicationRuntime(config)
        audit_client()
        detector = YoloOnnxDetector(
            config.model_path,
            confidence=config.confidence,
            expected_sha256=config.model_sha256 or None,
        )
        query(f"以前在哪里看到过{args.category}？没有历史就说明未知。", "find_object")
        created = query(f"未来三十分钟如果看到{args.category}请提醒我。", "create_watch")
        first_watch = watch_from(created)
        query("列出目前的关注任务。", "list_watches")
        query(f"取消关注任务 {first_watch}。", "cancel_watch")
        require(
            any(
                w["watch_id"] == first_watch and w["status"] == "cancelled"
                for w in runtime.watches.list_watches().data["watches"]
            ),
            "Cancellation not persisted",
        )
        appeared = watch_from(
            query(f"重新创建关注，未来三十分钟看到{args.category}时提醒我。", "create_watch")
        )
        source = start_video(detector)
        await_notification(appeared)
        query("现在画面里有哪些物品？", "get_current_scene")
        query(f"最后在哪里看到{args.category}，请给出时间和证据编号。", "find_object")
        query(f"查询最近一小时{args.category}的出现和未检测到事件。", "search_events")
        missing = watch_from(
            query(f"未来三十分钟如果持续未检测到{args.category}请提醒我。", "create_watch")
        )
        before_fault = len(events())
        source.mode = "fault"
        time.sleep(7)
        require(len(events()) == before_fault, "Injected disconnect generated a visual event")
        require(
            not runtime.memory.get_current_scene().data["current"], "Fault claimed current scene"
        )
        require(
            not any(n["watch_id"] == missing for n in notifications()), "Fault caused missing alert"
        )
        step("disconnect_does_not_mean_missing", seconds=7)
        source.mode = "video"
        wait_for(
            lambda: any(
                d["category"] == args.category
                for d in (runtime.memory.get_current_scene().data.get("observation") or {}).get(
                    "detections", []
                )
            )
        )
        time.sleep(4)
        source.mode = "blank"
        await_notification(missing)
        step(
            "controlled_blank_missing", limitation="Synthetic blank frames, not real object removal"
        )
        original_count = len(notifications())
        for event in events():
            require(
                runtime.watches.match_event(runtime.memory.get_event(event["event_id"])) == [],
                "Duplicate event rematched",
            )
        require(len(notifications()) == original_count, "Duplicate notice")
        idle = watch_from(
            query(f"未来六十分钟如果看到{args.idle_category}请提醒我。", "create_watch")
        )
        step("deduplicated_and_waiting_watch_created", notifications=original_count)
        runtime.close()
        require(runtime.last_error is None, "Runtime stop error")
        runtime = ApplicationRuntime(config)
        audit_client()
        require(
            not runtime.memory.get_current_scene().data["current"], "Restart claimed a live scene"
        )
        require(len(notifications()) == original_count, "Notifications changed on restart")
        require(runtime.memory.find_object(args.category).data["found"], "History lost on restart")
        require(
            any(
                w["watch_id"] == idle and w["status"] == "waiting"
                for w in runtime.watches.list_watches().data["watches"]
            ),
            "Waiting task not restored",
        )
        step("restart_restores_history_and_tasks", notifications=original_count)
        source = start_video(detector)
        wait_for(lambda: runtime._jobs.unfinished_tasks == 0)
        baseline_runs = len(runtime.memory.list_agent_runs())
        baseline_http = summary["http_audit"]["requests"]
        process = psutil.Process()
        process.cpu_percent()
        started = time.monotonic()
        samples = []
        while time.monotonic() - started < args.idle_seconds:
            time.sleep(max(0, min(5, args.idle_seconds - (time.monotonic() - started))))
            scene = runtime.memory.get_current_scene().data
            observation, _ = runtime.snapshot()
            samples.append(
                {
                    "elapsed": time.monotonic() - started,
                    "current": scene.get("current", False),
                    "rss_mb": process.memory_info().rss / 1024**2,
                    "cpu_percent_one_core_100": process.cpu_percent(),
                    "inference_ms": observation.inference_ms if observation else None,
                }
            )
            require(
                len(runtime.memory.list_agent_runs()) == baseline_runs, "Idle video woke real Agent"
            )
            require(summary["http_audit"]["requests"] == baseline_http, "Idle HTTP model request")
            require(runtime.last_error is None, "Runtime error during idle replay")
            if len(samples) % 12 == 0:
                print(
                    json.dumps(
                        {"idle_elapsed_seconds": round(samples[-1]["elapsed"]), "new_agent_runs": 0}
                    ),
                    flush=True,
                )
        summary["idle"] = {
            "elapsed_seconds": time.monotonic() - started,
            "requested_seconds": args.idle_seconds,
            "checks": len(samples),
            "invalid_checks": sum(not s["current"] for s in samples),
            "new_agent_runs": len(runtime.memory.list_agent_runs()) - baseline_runs,
            "new_http_requests": summary["http_audit"]["requests"] - baseline_http,
            "peak_rss_mb": max(s["rss_mb"] for s in samples),
            "mean_cpu_percent_one_core_100": sum(s["cpu_percent_one_core_100"] for s in samples)
            / len(samples),
        }
        (output / "idle-samples.json").write_text(json.dumps(samples, indent=2))
        require(summary["idle"]["invalid_checks"] == 0, "Invalid idle replay samples")
        step("connected_idle_video_passed", **summary["idle"])
        summary["passed"] = True
    except Exception as error:
        # Never include provider exception bodies/headers or config repr in an artifact.
        summary["failure_type"] = type(error).__name__
        if isinstance(error, AssertionError):
            summary["failure"] = str(error)
        print(json.dumps({"passed": False, "failure_type": type(error).__name__}), flush=True)
    finally:
        if runtime is not None:
            runtime.close()
            summary["runtime_error"] = runtime.last_error
            summary["agent_runs"] = runtime.memory.list_agent_runs()
            summary["notifications"] = notifications()
            summary["http_accounting_matches"] = summary["http_audit"]["requests"] == sum(
                r["request_attempts"] for r in summary["agent_runs"]
            )
            if not summary["http_accounting_matches"]:
                summary["passed"] = False
            if runtime.last_error:
                summary["passed"] = False
        summary["finished_at"] = datetime.now(UTC).isoformat()
        save()
        print(f"Local evidence: {output}", flush=True)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
