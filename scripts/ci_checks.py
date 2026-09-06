"""Run offline tests and expose concise failures in GitHub check annotations."""

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main():
    report = Path("data/ci-results.xml")
    report.parent.mkdir(exist_ok=True)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", f"--junitxml={report}"],
        check=False,
    )
    if completed.returncode and os.getenv("GITHUB_ACTIONS") == "true" and report.exists():
        root = ET.parse(report).getroot()
        for case in root.iter("testcase"):
            for failure in list(case.findall("failure")) + list(case.findall("error")):
                # Tests have no real credentials or camera data; retain only bounded test failures.
                detail = (failure.text or failure.attrib.get("message", "Test failed"))[-6000:]
                title = case.attrib.get("name", "Offline test failure")
                print(f"::error title={escape(title)}::{escape(detail)}", flush=True)
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
