from app.providers.landowner import classify


def test_no_federal_or_state_manager_is_said_plainly():
    m = classify({})
    assert m.category == "not_public" and m.label == "Not federal or state land"


def test_undetermined_is_not_printed_as_a_raw_code():
    m = classify({"ADMIN_AGENCY_CODE": "UND", "ADMIN_UNIT_NAME": "UND"})
    assert m.label == "Not federal or state land" and m.unit is None


def test_unknown_code_is_not_assumed_federal():
    m = classify({"ADMIN_AGENCY_CODE": "XYZ"})
    assert m.category == "other" and "XYZ" in m.label


def test_forest_service_is_national_forest_with_unit():
    m = classify({"ADMIN_AGENCY_CODE": "USFS", "ADMIN_UNIT_NAME": "Lassen"})
    assert m.category == "national_forest"
    assert m.unit == "Lassen National Forest"


def test_blm_and_state():
    assert classify({"ADMIN_AGENCY_CODE": "BLM"}).category == "blm"
    assert classify({"ADMIN_AGENCY_CODE": "ST"}).category == "state"
    assert classify({"ADMIN_AGENCY_CODE": "PVT"}).category == "private"
