"""Bounded, exact-observation still evidence around uncertain transitions."""

import hashlib
import json
from collections import deque
from pathlib import Path


class TransitionEvidence:
    def __init__(self, output: Path, max_groups: int = 8):
        self.output = output
        self.max_groups = max_groups
        self.before = deque(maxlen=4)
        self.groups = []
        self.active = None
        self.remaining = 0
        self.last_trigger = float("-inf")

    def observe(self, row: dict, jpeg: bytes | None, trigger: bool):
        if not row["fresh"] or jpeg is None:
            self.before.clear()
            self.active = None
            self.remaining = 0
            return
        item = (row, jpeg)
        if self.active is not None:
            self._save(item)
            self.remaining -= 1
            if not self.remaining:
                self.active = None
        elif (
            trigger
            and len(self.groups) < self.max_groups
            and row["monotonic_at"] - self.last_trigger >= 2
        ):
            self.last_trigger = row["monotonic_at"]
            self.active = {
                "index": len(self.groups),
                "trigger_sequence": row["frame_sequence"],
                "frames": [],
            }
            self.groups.append(self.active)
            for previous in self.before:
                self._save(previous)
            self._save(item)
            self.remaining = 4
        self.before.append(item)

    def _save(self, item):
        row, jpeg = item
        path = (
            self.output / f"diagnostics/{self.active['index']:02d}/{row['frame_sequence']:06d}.jpg"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(jpeg)
        metadata = {"observation": row, "jpeg_sha256": hashlib.sha256(jpeg).hexdigest()}
        path.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False))
        self.active["frames"].append(str(path.relative_to(self.output)))
