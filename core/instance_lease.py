"""桌面应用单实例租约。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QLockFile


class ApplicationInstanceLease:
    def __init__(self, lock_file: str | Path):
        self.lock_file = Path(lock_file)
        self._lock = QLockFile(str(self.lock_file))

    def try_acquire(self) -> bool:
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        return self._lock.tryLock(0)

    def release(self) -> None:
        self._lock.unlock()

    def __enter__(self):
        if not self.try_acquire():
            raise RuntimeError("EzYOLO 已在另一个窗口中运行")
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.release()


__all__ = ["ApplicationInstanceLease"]
