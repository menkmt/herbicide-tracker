from app.providers.landowner import classify


def test_no_federal_or_state_manager_means_private():
    m = classify({})
    assert m.category == "private" and m.label == "Private land"


def test_forest_service_is_national_forest_with_unit():
    m = classify({"ADMIN_AGENCY_CODE": "USFS", "ADMIN_UNIT_NAME": "Lassen"})
    assert m.category == "national_forest"
    assert m.unit == "Lassen National Forest"


def test_blm_and_state():
    assert classify({"ADMIN_AGENCY_CODE": "BLM"}).category == "blm"
    assert classify({"ADMIN_AGENCY_CODE": "ST"}).category == "state"
    assert classify({"ADMIN_AGENCY_CODE": "PVT"}).category == "private"
