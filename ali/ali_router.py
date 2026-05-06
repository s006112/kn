"""
Routing invariants (NON-NEGOTIABLE):

- Routing selects the execution routine and its constraints ONLY.
- Routing NEVER decides content, wording, or final answers.
- Routing MUST be deterministic and side-effect free.
- Routing output is read-only for all downstream steps.

Any logic that generates text, calls LLMs, or applies policy
MUST NOT be added to this module.
"""
# -----------------------------------------------------------------------------
# Execution routines (semantic contract)
#
# Routine A: safety_regulation
#   - allow RAG
#   - strict factual grounding
#   - reflection enabled
#
# Routine B: technical / commercial
#   - no RAG by default
#   - concise, factual generation
#
# Routine C: casual
#   - direct generation
#   - no reflection
#
# Routine D: unknown
#   - conservative default behavior
#
# NOTE:
# This table defines ALLOWED capabilities per routine.
# It does NOT define how those capabilities are implemented.
# -----------------------------------------------------------------------------


from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass
import re

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class RouteResult:
    category: str
    intent: str
    risk_level: str
    rationale: str
    confidence: float


def route_email(subject: str, body: str) -> RouteResult:
    text = f"{subject}\n{body}".lower()

    # -------------------------
    # intent (single pass)
    # -------------------------
    if "?" in text or re.search(r"\b(how|what)\b", text) or "can you" in text:
        intent, intent_reason = "ask_information", "question"
    elif "please provide" in text or re.search(r"\b(send|confirm)\b", text):
        intent, intent_reason = "request_action", "imperative"
    elif "as discussed" in text or "following up" in text:
        intent, intent_reason = "follow_up", "follow-up"
    elif re.search(r"\b(problem|issue)\b", text) or "not acceptable" in text:
        intent, intent_reason = "complaint", "negative"
    else:
        intent, intent_reason = "statement", "default"

    # -------------------------
    # category + risk
    # -------------------------
    if re.search(r"\b(rita|ritasoo)\b", text):
        return RouteResult(
            category="rita",
            intent=intent,
            risk_level="medium",
            rationale="rita keyword",
            confidence=0.7,
        )

    if re.search(r"\b(iec|ul|en|ce|csa|certification|certificates?|compliance|standards?)\b", text):
        return RouteResult(
            category="safety_regulation",
            intent=intent,
            risk_level="high",
            rationale="safety keywords",
            confidence=0.9,
        )

    if re.search(r"\b(specifications?|test(?:ing|s)?|wiring|voltage|current|power|dimensions?)\b", text):
        return RouteResult(
            category="technical",
            intent=intent,
            risk_level="medium",
            rationale="technical keywords",
            confidence=0.7,
        )

    if re.search(r"\b(price|quotations?|moq|payment)\b", text) or "lead time" in text:
        return RouteResult(
            category="commercial",
            intent=intent,
            risk_level="medium",
            rationale="commercial keywords",
            confidence=0.7,
        )

    if re.search(
        r"\b(hi|hello|thanks|ok|okay|noted|meeting|schedule|call|delivery|shipment|logistics)\b",
        text,
    ) or re.search(r"\b(thank you|good (morning|afternoon|evening))\b", text):
        return RouteResult(
            category="casual",
            intent=intent,
            risk_level="low",
            rationale="casual language",
            confidence=0.6,
        )

    return RouteResult(
        category="unknown",
        intent="unknown",
        risk_level="low",
        rationale="no match",
        confidence=0.3,
    )
