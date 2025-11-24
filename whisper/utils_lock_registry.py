import threading
from contextlib import contextmanager
from typing import Any, Callable, Dict


_file_locks: Dict[str, threading.Lock] = {}
_file_locks_mutex = threading.Lock()


def acquire_file_lock(file_path: str) -> bool:
    """Acquire a non-blocking lock for the given file path."""
    with _file_locks_mutex:
        if file_path not in _file_locks:
            _file_locks[file_path] = threading.Lock()
        file_lock = _file_locks[file_path]
    return file_lock.acquire(blocking=False)


def release_file_lock(file_path: str) -> None:
    """Release the lock for the given file path if it exists."""
    with _file_locks_mutex:
        if file_path in _file_locks:
            _file_locks[file_path].release()


def cleanup_file_lock(file_path: str) -> None:
    """Remove the lock entry for the given file path if present."""
    with _file_locks_mutex:
        if file_path in _file_locks:
            del _file_locks[file_path]


@contextmanager
def file_lock(file_path: str):
    """Context manager to safely acquire and release a file lock."""
    if acquire_file_lock(file_path):
        try:
            yield True
        finally:
            release_file_lock(file_path)
            cleanup_file_lock(file_path)
    else:
        yield False


def get_file_lock_functions() -> Dict[str, Callable[[str], Any]]:
    """Expose file lock functions for integration points (e.g., wikilink cleaner)."""
    return {
        "acquire": acquire_file_lock,
        "release": release_file_lock,
        "cleanup": cleanup_file_lock,
    }

