from inventra.llm.validator import validate

FACTS = {
    "facts": [
        {"id": "F1", "label": "Recommended option", "value": "Transfer 180 Dubai -> Abu Dhabi", "tag": "Estimate"},
        {"id": "F2", "label": "Abu Dhabi P50 demand, 30 days", "value": 350, "unit": "units", "tag": "Estimate"},
        {"id": "F3", "label": "Lost sales avoided vs Do nothing", "value": 170, "unit": "units", "tag": "Estimate"},
        {"id": "F4", "label": "Net benefit vs Do nothing", "value": 4728.4, "unit": "AED", "tag": "Estimate"},
        {"id": "F5", "label": "PO-104 delay from SIG-001", "value": 7, "unit": "days", "tag": "Assumption"},
    ],
    "reasons": [
        {"id": "R1", "text": "Dubai-only transfer causes lost sales in Dubai under P90 demand", "tag": "Estimate"},
        {"id": "R2", "text": "SIG-001 delays PO-104 into Abu Dhabi", "tag": "Assumption"},
    ],
}


def test_good_text_passes_and_counts_figures():
    text = ("Move 180 units from Dubai to Abu Dhabi [F1]. "
            "This avoids 170 lost units because Abu Dhabi needs 350 units over 30 days [F3, F2, R1]. "
            "The main risk is the assumption that PO-104 slips 7 days [F5, R2].")
    ok, errors, n = validate(text, FACTS, allow=[30])
    assert ok, errors
    assert n == 5


def test_wrong_number_rejected():
    ok, errors, _ = validate("Move 190 units [F1]. It avoids 170 units because of demand [F3, R1]. Risk is low [F4].", FACTS)
    assert not ok and any("190" in e for e in errors)


def test_cause_without_reason_id_rejected():
    ok, errors, _ = validate("Move 180 units [F1]. This is needed because demand is 350 units [F2]. Net is 4728.4 AED [F4].", FACTS)
    assert not ok and any("reason ID" in e for e in errors)


def test_assumption_must_be_named():
    ok, errors, _ = validate("Move 180 units [F1]. It avoids 170 units due to the gap [F3, R1]. PO-104 slips 7 days [F5].", FACTS)
    assert not ok and any("assumption" in e for e in errors)


def test_unknown_sku_and_number_words_rejected():
    ok, errors, _ = validate("Move 180 units [F1]. Also move CHG-02 because of demand [R1]. It saves five hundred AED [F4].", FACTS)
    assert not ok
    assert any("CHG-02" in e for e in errors)
    assert any("number word" in e for e in errors)


def test_thousands_separator_normalised():
    ok, errors, _ = validate("Net benefit is 4,728.4 AED [F4]. It avoids 170 units because of the shortfall [F3, R1]. "
                             "Move 180 units [F1].", FACTS)
    assert ok, errors
