"""
Responsibility:
Centralizes pipeline shared state (queues, locks, processed sets, and shutdown flag) and
exposes a factory to build a fresh context for a given config.

Used by:
* whisper/p_pretext.py
* whisper/p_pipelines.py
* whisper/p_orchestrator.py

Pipelines:
- config -> PipelineContext

Invariants:
- Context fields are initialized with empty queues, locks, and sets on construction.
- create_pipeline_context returns a new PipelineContext containing the provided config.

Out of scope:
- Running pipeline steps or orchestrating threads.
- Persisting or loading state.
"""

from dataclasses import dataclass, field
import threading
from queue import Queue
from typing import Any, Dict, Set


@dataclass
class PipelineContext:
    """
    Purpose:
    Hold shared state for pipeline stages in a single object.

    Inputs:
    config: Configuration mapping used by pipeline stages.

    Outputs:
    PipelineContext instance with queues, locks, and tracking state initialized.

    Side effects:
    Allocates new Queue, Lock, and Event objects.

    Failure modes:
    None.
    """

    config: Dict[str, Any]
    pretext_queue: Queue = field(default_factory=Queue)
    extract_queue: Queue = field(default_factory=Queue)
    premium_extract_queue: Queue = field(default_factory=Queue)
    text_processing_lock: threading.Lock = field(default_factory=threading.Lock)
    audio_processing_lock: threading.Lock = field(default_factory=threading.Lock)
    processed_files_global: Set[str] = field(default_factory=set)
    processed_files_lock: threading.Lock = field(default_factory=threading.Lock)
    wikilink_cleaning_stats: Dict[str, Any] = field(
        default_factory=lambda: {"last_run": None, "cycle_count": 0}
    )
    shutdown_flag: threading.Event = field(default_factory=threading.Event)


def create_pipeline_context(cfg: Dict[str, Any]) -> PipelineContext:
    """
    Purpose:
    Create a new PipelineContext for the provided configuration.

    Inputs:
    cfg: Configuration mapping to store in the context.

    Outputs:
    PipelineContext initialized with cfg and fresh queues, locks, and flags.

    Side effects:
    Allocates new Queue, Lock, and Event objects.

    Failure modes:
    None.
    """
    return PipelineContext(config=cfg)
