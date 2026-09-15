"""An operating-system file lock that survives PID-namespace differences.

The lock is released by the kernel when the process exits, even after a
forced stop. Its on-disk file is deliberately retained to avoid inode races.
"""

import os
import sys

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl
from pathlib import Path


class InstanceLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd = None

    def acquire(self) -> bool:
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0), 0o600)
        try:
            if sys.platform == "win32":
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError):
            os.close(fd)
            return False
        except OSError:
            os.close(fd)
            raise
        self.fd = fd
        return True

    def release(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
