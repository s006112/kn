"""
PipelineContext 定义：集中存储队列、锁、状态，供 pipelines 使用。
与 orchestrator/pipelines 构成的路径为 p.py → p_orchestrator.py → (p_context.py + p_pipelines.py)。
"""

from dataclasses import dataclass, field
import threading
from queue import Queue
from typing import Any, Dict, Set


@dataclass
class PipelineContext:
    """集中存储 pipeline 所需的共享状态，减少零散全局变量。"""

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
    为给定配置创建一个新的 PipelineContext。

    每次系统启动时重新创建，确保队列/锁等状态都是干净的。
    """
    return PipelineContext(config=cfg)
