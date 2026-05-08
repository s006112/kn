"""
utils_lock_registry.py
In-process registry of non-blocking per-path locks used by pipeline workers.

Used by:
* w/p_pipelines.py

Pipelines:
- file_path -> registry_lookup -> acquire_nonblocking -> locked_bool
- file_path -> registry_lookup -> release -> cleanup
- file_path -> contextmanager -> yield_locked -> finally_release

Invariants:
- Locks are stored in `_file_locks` keyed by the provided `file_path` string.
- `acquire_file_lock` attempts a non-blocking acquire and returns a boolean.
- `safe` context usage is provided by `file_lock`, which releases and cleans up on success.
- `get_file_lock_functions` returns the current function objects for integration points.

Out of scope:
- Cross-process locking and OS-level file locks.
- Deadlock detection and fairness guarantees.
- Reference counting of lock users and long-lived lock reuse policies.
"""

import threading
from contextlib import contextmanager
from typing import Any, Callable, Dict


_file_locks: Dict[str, threading.Lock] = {}
_file_locks_mutex = threading.Lock()


def acquire_file_lock(file_path: str) -> bool:
    """Acquire a non-blocking in-process lock for `file_path`."""
    with _file_locks_mutex:
        if file_path not in _file_locks:
            _file_locks[file_path] = threading.Lock()
        file_lock = _file_locks[file_path]
    return file_lock.acquire(blocking=False)


def release_file_lock(file_path: str) -> None:
    """Release the registered lock for `file_path` when present."""
    with _file_locks_mutex:
        if file_path in _file_locks:
            _file_locks[file_path].release()


def cleanup_file_lock(file_path: str) -> None:
    """Remove the registered lock entry for `file_path` when present."""
    with _file_locks_mutex:
        if file_path in _file_locks:
            del _file_locks[file_path]


@contextmanager
def file_lock(file_path: str):
    """Yield whether a non-blocking lock for `file_path` was acquired."""
    if acquire_file_lock(file_path):
        try:
            yield True
        finally:
            release_file_lock(file_path)
            cleanup_file_lock(file_path)
    else:
        yield False


def get_file_lock_functions() -> Dict[str, Callable[[str], Any]]:
    """Return the file-lock operation mapping used by integration points."""
    return {
        "acquire": acquire_file_lock,
        "release": release_file_lock,
        "cleanup": cleanup_file_lock,
    }
