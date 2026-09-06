"""Native process lock: two local app processes must not own the same runtime data."""

import os
from pathlib import Path


class InstanceLock:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a+b")
        self._locked = False
        try:
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                self._file.write(b"0")
                self._file.flush()
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._locked = True
        except OSError as exc:
            self._file.close()
            raise RuntimeError("此数据目录已有应用实例运行，请先停止原实例。") from exc

    def close(self):
        if not self._locked:
            return
        if os.name == "nt":
            import msvcrt

            self._file.seek(0)
            msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._locked = False
        self._file.close()
