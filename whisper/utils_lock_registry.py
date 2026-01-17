"""
In-process registry of non-blocking per-path locks used by pipeline workers.

Used by:
* whisper/p_pipelines.py

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
    """
    Purpose:
    - Acquire a non-blocking in-process lock associated with `file_path`.
    Inputs:
    - file_path: Key used to select/create a lock in the registry.
    Outputs:
    - True when the lock is acquired, otherwise False.
    Side effects:
    - May create and store a new `threading.Lock` in `_file_locks`.
    Failure modes:
    - Propagates unexpected exceptions from threading primitives.
    """
    with _file_locks_mutex:
        if file_path not in _file_locks:
            _file_locks[file_path] = threading.Lock()
        file_lock = _file_locks[file_path]
    return file_lock.acquire(blocking=False)


def release_file_lock(file_path: str) -> None:
    """
    Purpose:
    - Release an existing lock associated with `file_path`.
    Inputs:
    - file_path: Registry key for the lock to release.
    Outputs:
    - None.
    Side effects:
    - Calls `.release()` on the stored `threading.Lock` when present.
    Failure modes:
    - May raise `RuntimeError` if the lock is not held by any thread at release time.
    """
    with _file_locks_mutex:
        if file_path in _file_locks:
            _file_locks[file_path].release()


def cleanup_file_lock(file_path: str) -> None:
    """
    Purpose:
    - Remove the lock entry for `file_path` from the registry.
    Inputs:
    - file_path: Registry key to delete.
    Outputs:
    - None.
    Side effects:
    - Deletes an entry from `_file_locks` when present.
    Failure modes:
    - None.
    """
    with _file_locks_mutex:
        if file_path in _file_locks:
            del _file_locks[file_path]


@contextmanager
def file_lock(file_path: str):
    """
    Purpose:
    - Provide a context manager that attempts to lock `file_path` non-blocking.
    Inputs:
    - file_path: Registry key to lock for the duration of the context.
    Outputs:
    - Yields True when the lock was acquired, otherwise yields False.
    Side effects:
    - On success, releases and removes the lock entry on context exit.
    Failure modes:
    - Propagates exceptions from `release_file_lock` when releasing an unheld lock.
    """
    if acquire_file_lock(file_path):
        try:
            yield True
        finally:
            release_file_lock(file_path)
            cleanup_file_lock(file_path)
    else:
        yield False


def get_file_lock_functions() -> Dict[str, Callable[[str], Any]]:
    """
    Purpose:
    - Provide a dictionary of file-lock operations for consumers expecting this shape.
    Inputs:
    - None.
    Outputs:
    - Mapping with keys `acquire`, `release`, and `cleanup`.
    Side effects:
    - None.
    Failure modes:
    - None.
    """
    return {
        "acquire": acquire_file_lock,
        "release": release_file_lock,
        "cleanup": cleanup_file_lock,
    }
