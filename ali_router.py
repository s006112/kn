from __future__ import annotations

from dataclasses import dataclass
import re


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
    if re.search(r"\b(iec|ul|en|ce|certification|compliance|standards?)\b", text):
        return RouteResult(
            category="safety_regulation",
            intent=intent,
            risk_level="high",
            rationale="safety keywords",
            confidence=0.9,
        )

    if re.search(r"\b(specifications?|wiring|voltage|current|power|dimensions?)\b", text):
        return RouteResult(
            category="technical",
            intent=intent,
            risk_level="medium",
            rationale="technical keywords",
            confidence=0.7,
        )

    if re.search(r"\b(price|quotation|moq|payment)\b", text) or "lead time" in text:
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
