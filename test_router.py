from ali_router import route_email


def test_safety_regulation_email():
    result = route_email("IEC certification", "Do you have IEC 60335 compliance?")
    assert result.category == "safety_regulation"
    assert result.intent == "ask_information"
    assert result.risk_level == "high"
    assert 0.0 <= result.confidence <= 1.0


def test_technical_question():
    result = route_email("Wiring specs", "What is the voltage and current?")
    assert result.category == "technical"
    assert result.intent == "ask_information"
    assert result.risk_level == "medium"


def test_commercial_quotation_request():
    result = route_email("Quotation request", "Please provide price and MOQ.")
    assert result.category == "commercial"
    assert result.intent == "request_action"
    assert result.risk_level == "medium"


def test_casual_reply():
    result = route_email("Thanks", "Thanks, see you at the meeting tomorrow.")
    assert result.category == "casual"
    assert result.intent == "statement"
    assert result.risk_level == "low"


def test_unknown_content():
    result = route_email("FYI", "Just sharing the update.")
    assert result.category == "unknown"
    assert result.intent == "unknown"
    assert result.risk_level == "low"
