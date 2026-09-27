"""T1e: safe temporary file management and cleanup. Standard library only.

TempPair(generated, real) -> context manager
sweep(dir, max_age_s, now=None) -> int
"""
import os
import time

IMPLEMENTED = True


class TempPair:
    """Context manager ensuring temporary photos are deleted on exit."""

    def __init__(self, generated, real):
        self.generated = generated
        self.real = real

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for path in (self.generated, self.real):
            if path is not None:
                try:
                    os.remove(path)
                except OSError:
                    pass
        return False


def sweep(dir, max_age_s, now=None):
    """Remove files in dir older than max_age_s seconds. Subdirectories are preserved."""
    if now is None:
        now = time.time()
    if not os.path.isdir(dir):
        return 0

    removed = 0
    try:
        with os.scandir(dir) as entries:
            for entry in entries:
                try:
                    if entry.is_file():
                        if (now - entry.stat().st_mtime) > max_age_s:
                            os.remove(entry.path)
                            removed += 1
                except OSError:
                    continue
    except OSError:
        return 0

    return removed
