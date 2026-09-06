from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_import_disables_ort_telemetry_before_native_module_load(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment.pop("ORT_DISABLE_TELEMETRY", None)
    result = subprocess.run(
        [sys.executable, "-c", "import visual_ai_agent.vision"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / ":memory:.ses").exists()
    assert "telemetry" not in result.stderr.lower()
