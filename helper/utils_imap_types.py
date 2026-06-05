from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class EmailMessage:
    uid: int
    message_id: str
    from_addr: str
    to_addrs: List[str]
    cc_addrs: List[str]
    subject: str
    body_text: str
    raw_bytes: bytes
    from_name: str = ""


@dataclass
class SendResult:
    ok: bool
    error_message: Optional[str] = None
