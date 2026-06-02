"""
Routing invariants (NON-NEGOTIABLE):

- Routing selects the execution routine and its constraints ONLY.
- Routing NEVER decides content, wording, or final answers.
- Routing MUST be deterministic and side-effect free.
- Routing output is read-only for all downstream steps.

Any logic that generates text, calls LLMs, performs retrieval, sends email,
marks messages SEEN, or applies delivery policy MUST NOT be added here.

Execution routines (semantic contract):

- safety_regulation
  - may allow standard RAG via ali_llm gate
  - requires stricter factual grounding

- technical
  - may allow standard RAG via ali_llm gate
  - used for product, specification, testing, wiring, voltage, current,
    power, and dimension related questions

- rita
  - may allow rita RAG via ali_llm gate
  - used for historical Rita-related context lookup

- commercial
  - no RAG by default
  - concise, factual generation

- casual
  - no RAG by default
  - direct generation

- unknown
  - conservative default behavior

This module only classifies the email into a route.
The actual RAG decision is owned by ali_llm.py.

Used by:
- ali_llm.py
"""

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
