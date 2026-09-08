"""Print shareable runtime facts; never opens a camera or sends a model request."""

import hashlib
import json
import platform
import sys
from importlib.metadata import version
from pathlib import Path

# Also supports direct script execution from the repository checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visual_ai_agent.config import Config  # noqa: E402


def main():
    config = Config.from_env()
    model_exists = config.model_path.is_file()
    report = {
        "python": platform.python_version(),
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "packages": {
            name: version(name)
            for name in (
                "openai-agents",
                "openai",
                "opencv-python",
                "onnxruntime",
                "supervision",
                "streamlit",
                "pydantic",
                "psutil",
            )
        },
        "agent_configured": config.agent_connected,
        "model_exists": model_exists,
        "model_sha256": hashlib.sha256(config.model_path.read_bytes()).hexdigest()
        if model_exists
        else None,
        "sample_interval_seconds": config.sample_interval,
        "requested_camera_size": [config.camera_width, config.camera_height],
        "observation_region": config.observation_region,
        "camera_tested": False,
        "live_api_tested": False,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
