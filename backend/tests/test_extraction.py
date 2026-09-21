"""Extraction, against fixtures modelled on the real county documents."""

from __future__ import annotations

import pytest

from app.extraction import permit, pur_form, tabular
from app.extraction.base import ApplicationMethod, IssueCode, normalize_method
from app.extraction.fieldmap import (
    match_label,
    parse_datetime,
    split_amount_units,
    split_commodity,
)
from app.extraction.registry import detect, extract_file


class TestFieldMapping:
    @pytest.mark.parametrize(
        ("label", "field"),
        [
            ("Permitee", "operator_name"),
            ("Permittee/Permit Operator", "operator_name"),
            ("Site ID", "site_id"),
            ("Site Identification Number", "site_id"),
            ("Operator ID/Permit Number", "permit_number"),
            ("Commercial Applicator (if any)", "applicator_name"),
            ("Appl. Method", "method"),
            ("App Method/Fume Code", "method"),
            ("Start Date/Time Applied", "start_datetime"),
            ("Section (MTRS)", "mtrs_text"),
            ("EPA Reg No", "epa_reg_no"),
        ],
    )
    def test_county_labels_map_to_one_field(self, label, field):
        """Different counties label the same fact differently."""
        assert match_label(label) == field

    def test_an_unknown_label_is_not_forced_onto_a_field(self):
        assert match_label("Entirely Unknown Column") is None

    def test_dates_and_times_combine_from_either_layout(self):
        combined = parse_datetime("7/19/2024 6:00 AM")
        separate = parse_datetime("07/19/2024", time_value="6:00 AM")
        assert combined == separate

    def test_amounts_split_from_their_units(self):
        assert split_amount_units("63 ACRES") == (63.0, "ACRES")
        assert split_amount_units("1,050") == (1050.0, None)

    def test_commodity_codes_parse_in_either_order(self):
        assert split_commodity("FOREST, TMBRLND / 30000-0") == ("FOREST, TMBRLND", "30000-0")
        assert split_commodity("30000 FOREST, TMBRLND") == ("FOREST, TMBRLND", "30000")

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Ground", ApplicationMethod.GROUND),
            ("Aircraft", ApplicationMethod.AERIAL),
            ("Air/Ground", ApplicationMethod.AERIAL),
            (None, ApplicationMethod.UNKNOWN),
        ],
    )
    def test_methods_normalise_to_aerial_or_ground(self, raw, expected):
        assert normalize_method(raw) == expected


class TestTabularExport:
    def test_reads_a_county_use_record_export(self, fixtures_dir):
        result = tabular.extract(fixtures_dir / "use_records.tsv", county="Lassen")
        assert result.record_count == 2

        first = result.records[0]
        assert first.document_number == "WEB2115761161"
        assert first.operator_name == "WM BEATY AND ASSOC."
        assert first.applicator_name == "FOREST PROTECTION"
        assert first.mtrs == "M34N10E03"
        assert first.method == ApplicationMethod.GROUND

    def test_rows_sharing_a_document_number_are_one_application(self, fixtures_dir):
        """A tank mix is several rows but one application."""
        result = tabular.extract(fixtures_dir / "use_records.tsv", county="Lassen")
        first = result.records[0]
        assert len(first.products) == 2
        assert {p.product_name for p in first.products} == {"TRANSLINE", "SUPER SPREAD MSO"}

    def test_california_distributor_suffix_is_stripped_for_lookup(self, fixtures_dir):
        result = tabular.extract(fixtures_dir / "use_records.tsv", county="Lassen")
        product = result.records[0].products[0]
        assert product.epa_reg_no == "62719-259-AA"
        assert product.base_epa_reg_no == "62719-259"
        assert product.distributor_suffix == "AA"

    def test_explicit_columns_make_the_location_verified(self, fixtures_dir):
        result = tabular.extract(fixtures_dir / "use_records.tsv", county="Lassen")
        assert result.records[0].site.confidence == "verified"

    def test_every_field_keeps_its_source(self, fixtures_dir):
        result = tabular.extract(fixtures_dir / "use_records.tsv", county="Lassen")
        sources = result.records[0].field_sources
        assert "operator_name" in sources
        assert sources["operator_name"].source_name == "use_records.tsv"
        assert "line" in (sources["operator_name"].locator or "")

    def test_a_file_that_is_not_an_export_is_rejected(self, tmp_path):
        path = tmp_path / "notes.txt"
        path.write_text("just some prose about nothing in particular\n")
        result = tabular.extract(path)
        assert result.record_count == 0
        assert result.issues


class TestUseReportForm:
    def test_reads_a_form_laid_out_as_a_grid(self, fixtures_dir):
        result = pur_form.extract(fixtures_dir / "use_report_form.txt", county="Lassen")
        record = result.records[0]
        assert record.document_number == "WEB2419159565"
        assert record.operator_name == "Mike's Land Mgmt"
        assert record.applicator_name == "HIGHLANDERS FOREST, LLC"

    def test_a_non_plss_site_id_falls_back_to_the_section_boxes(self, fixtures_dir):
        """'TBarr' is a site name, but the form still says where it is."""
        result = pur_form.extract(fixtures_dir / "use_report_form.txt", county="Lassen")
        record = result.records[0]
        assert record.site_id == "TBarr"
        assert record.mtrs == "M29N11E27"
        assert not any(i.code == IssueCode.MISSING_LOCATION for i in record.issues)

    def test_the_location_box_carries_the_landowner(self, fixtures_dir):
        result = pur_form.extract(fixtures_dir / "use_report_form.txt", county="Lassen")
        assert result.records[0].location_text == "Terry Barr"

    def test_start_and_end_times_give_a_real_date_range(self, fixtures_dir):
        result = pur_form.extract(fixtures_dir / "use_report_form.txt", county="Lassen")
        record = result.records[0]
        assert record.start_datetime.hour == 6
        assert record.end_datetime.hour == 14

    def test_a_form_with_no_location_at_all_is_flagged(self, tmp_path):
        path = tmp_path / "form.txt"
        path.write_text(
            "Pesticide Use Report\n"
            "Permittee/Permit Operator: Someone\n"
            "Site Identification Number: TBarr\n"
            "Application Date: 7/19/2024\n"
            "Product Name: ROUNDUP\n"
        )
        record = pur_form.extract(path, county="Lassen").records[0]
        assert any(i.code == IssueCode.MISSING_LOCATION for i in record.issues)


class TestRestrictedMaterialsPermit:
    def test_reads_the_three_permit_tables(self, fixtures_dir):
        result = permit.extract(fixtures_dir / "permit.docx_text.txt", county="Lassen")
        parsed = result.permits[0]
        assert parsed.permit_number == "18-24-4500033"
        assert parsed.operator_name == "WM BEATY AND ASSOC"
        assert parsed.expires_on.isoformat() == "2026-12-31"
        assert len(parsed.contacts) == 4
        assert len(parsed.permitted_materials) == 3
        assert len(parsed.sites) == 3

    def test_the_contact_list_yields_licences(self, fixtures_dir):
        result = permit.extract(fixtures_dir / "permit.docx_text.txt", county="Lassen")
        contacts = {c.name: c for c in result.permits[0].contacts}
        helicopter = contacts["WESTERN HELICOPTER SERVICES"]
        assert helicopter.license_number == "30717"
        assert helicopter.contact_type == "PCM"

    def test_the_non_restricted_placeholder_is_not_a_restricted_material(self, fixtures_dir):
        result = permit.extract(fixtures_dir / "permit.docx_text.txt", county="Lassen")
        names = {m["name"] for m in result.permits[0].permitted_materials}
        assert "2,4-D" in names
        assert "STRYCHNINE" in names

    def test_the_site_list_decodes_and_carries_permitted_acreage(self, fixtures_dir):
        result = permit.extract(fixtures_dir / "permit.docx_text.txt", county="Lassen")
        sites = {s.site_id: s for s in result.permits[0].sites}
        assert sites["280801"].site.mtrs == "M28N08E01"
        assert sites["280801"].acreage == 645.0

    def test_a_site_id_disagreeing_with_its_mtrs_is_flagged(self, fixtures_dir):
        """Real permits contain these; they must never be resolved by guessing."""
        result = permit.extract(fixtures_dir / "permit_conflict.txt", county="Lassen")
        parsed = result.permits[0]
        conflicted = [s for s in parsed.sites if s.issues]
        assert conflicted
        assert any(i.code == IssueCode.SITE_ID_MTRS_CONFLICT for i in parsed.issues)


class TestFormatDetection:
    @pytest.mark.parametrize(
        ("filename", "profile"),
        [
            ("use_records.tsv", "tabular_use_records"),
            ("use_report_form.txt", "pur_form"),
            ("permit.docx_text.txt", "restricted_materials_permit"),
        ],
    )
    def test_files_are_identified_by_content(self, fixtures_dir, filename, profile):
        assert detect(fixtures_dir / filename).profile.name == profile

    def test_an_unrecognised_file_is_reported_not_dropped(self, tmp_path):
        path = tmp_path / "random.txt"
        path.write_text("nothing to do with pesticides at all\n")
        result = extract_file(path)
        assert result.record_count == 0
        assert result.issues[0].code == "unrecognised_format"

    def test_out_of_coverage_records_are_flagged(self, tmp_path):
        path = tmp_path / "old.tsv"
        path.write_text(
            "Permit #\tPermitee\tSite ID\tMeridian\tTownship\tRange\tSection\t"
            "Application Date\tProduct Name\tEPA Reg No\tQuantity Used\tQuantity Units\t"
            "Treated Amount\tAppl. Method\tCommodity\n"
            "4500033\tWM BEATY AND ASSOC.\t341003\tM\t34N\t10E\t03\t"
            "07/01/2019\tTRANSLINE\t62719-259-AA\t12\tOunce\t12.6\tGround\tFOREST, TMBRLND\n"
        )
        result = extract_file(path, county="Lassen")
        record = result.records[0]
        assert not record.in_coverage
        assert any(i.code == IssueCode.OUT_OF_COVERAGE for i in record.issues)
