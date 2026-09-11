"""Exercise real runtime/model consumers against one paced local development video.

Only the camera transport is substituted. No fake detections, cloud calls, or
independent acceptance claims are made. Run separately from training for timing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from scripts.check_behavior_integration import run
from scripts.training_source_policy import verify_development_sources
from visual_ai_agent.vision import ReplaySource


def run_replay(args):
    sources = verify_development_sources([args.video.resolve()], args.registry.resolve())
    args.source_description = "paced looping development video; real models; no action labels"
    args.camera = 0
    transports = []

    class CountedReplay(ReplaySource):
        def __init__(self):
            super().__init__(args.video, loop=True, realtime=True)
            self.opens = 0

        def open(self):
            self.opens += 1
            super().open()

    def create_source(**_):
        source = CountedReplay()
        transports.append(source)
        return source

    with patch("visual_ai_agent.runtime.CameraSource", create_source):
        code = run(args)
    path = args.output.resolve() / "integration-summary.json"
    summary = json.loads(path.read_text())
    summary["verified_development_sources"] = sources
    summary["source_instances"] = len(transports)
    summary["source_open_counts"] = [source.opens for source in transports]
    summary["single_source_passed"] = len(transports) == 1 and transports[0].opens == 1
    summary["provenance_passed"] = all(
        set(summary.get("observation_sources", {}).get(name, {})) == {"replay"}
        for name in ("objects", "behavior")
    )
    summary["loop_warning"] = "Loop boundaries are artificial; events/statistics are not scored."
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return code or int(not summary["single_source_passed"] or not summary["provenance_passed"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("harness/evaluations/recognition-20260911-sources.json"),
    )
    parser.add_argument("--posture-manifest", type=Path, required=True)
    parser.add_argument("--drinking-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, choices=[30, 60, 300, 1800], default=60)
    raise SystemExit(run_replay(parser.parse_args()))
