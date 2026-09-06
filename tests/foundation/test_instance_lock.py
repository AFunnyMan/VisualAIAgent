import pytest

from visual_ai_agent.instance_lock import InstanceLock


def test_exclusive_owner_and_release(tmp_path):
    path = tmp_path / "app.lock"
    first = InstanceLock(path)
    with pytest.raises(RuntimeError, match="已有应用实例"):
        InstanceLock(path)
    first.close()
    second = InstanceLock(path)
    second.close()
    second.close()
