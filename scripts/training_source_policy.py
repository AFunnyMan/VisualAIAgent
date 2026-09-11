"""Enforce explicit source provenance for the fixed-scene training experiments."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def verify_development_sources(paths: list[Path], registry: Path) -> dict[str, str]:
    """Reject unknown, duplicated or held-out videos, including renamed copies.

    Call before extracting frames. The returned mapping belongs in the derived
    dataset manifest so training provenance does not depend on file names.
    """
    rows = json.loads(registry.read_text(encoding="utf-8"))["sources"]
    purposes: dict[str, str] = {}
    for row in rows:
        digest, purpose = row["sha256"], row["purpose"]
        if purpose not in {"development", "holdout"} or digest in purposes:
            raise ValueError("Invalid or ambiguous source registry")
        purposes[digest] = purpose
    verified: dict[str, str] = {}
    for path in paths:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if purposes.get(digest) != "development":
            raise ValueError(f"Source is held out or unregistered: {path.name}")
        if digest in verified.values():
            raise ValueError("Repeated source content")
        verified[str(path.resolve())] = digest
    if not verified:
        raise ValueError("No development sources supplied")
    return verified
