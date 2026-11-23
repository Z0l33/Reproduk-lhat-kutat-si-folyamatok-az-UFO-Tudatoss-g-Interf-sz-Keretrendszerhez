import json
import tempfile
import unittest
from pathlib import Path

from ufo_pipeline.gep_qa import analyse
from ufo_pipeline.hashing import sha256_file

HEADER = ("Gemeldet;Fallnummer;Datum;Land;Bundesland;PLZ;PLZ_unvollstaendig;Sichtungsort;lokale Uhrzeit;"
          "Zeitzone;Tageszeit;UTC;Erstkontakt;Sachverhalt;Klassifikation_Hynek;Klassifikation_Hendry;"
          "Klassifikation_IFO-Verifikationsschluessel;Identifikation;Identifikations_Gruppe")


def line(**kw):
    d = dict(Gemeldet="2020-01-02", Fallnummer="1-0001", Datum="2020-01-01", Land="Deutschland",
             Bundesland="Bayern", PLZ="80331", PLZ_unvollstaendig="", Sichtungsort="Ortsname",
             **{"lokale Uhrzeit": "21:30"}, Zeitzone="MEZ", Tageszeit="", UTC="20:30", Erstkontakt="",
             Sachverhalt="neutral text", Klassifikation_Hynek="NL", Klassifikation_Hendry="IFO",
             **{"Klassifikation_IFO-Verifikationsschluessel": "V1"}, Identifikation="", Identifikations_Gruppe="")
    d.update(kw)
    return ";".join(d[h] for h in HEADER.split(";"))


class QA(unittest.TestCase):
    def run_qa(self, *lines):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.csv"
            p.write_text("\n".join([HEADER, *lines]) + "\n", encoding="utf-8")
            before = sha256_file(p)
            rep = analyse(p)
            self.assertEqual(sha256_file(p), before)
            return rep

    def test_no_free_text_is_echoed(self):
        secret = "GEHEIMERTEXT"
        rep = self.run_qa(line(Sachverhalt=f"{secret} Herr Müller ich", Sichtungsort=f"{secret}ort 5"))
        self.assertNotIn(secret, json.dumps(rep, ensure_ascii=False))
        self.assertNotIn("Müller", json.dumps(rep, ensure_ascii=False))

    def test_input_unchanged_flag(self):
        self.assertTrue(self.run_qa(line())["input"]["unchanged_during_analysis"])

    def test_date_rules(self):
        rep = self.run_qa(line(Fallnummer="a", Gemeldet="1970-01-01", Datum="1925-02-08"),
                          line(Fallnummer="b", Gemeldet="2019-01-01", Datum="2020-01-01"),
                          line(Fallnummer="c", Datum="2027-01-01"))
        d = rep["dates"]
        self.assertEqual(d["Datum"]["before_1972"], 1)
        self.assertEqual(d["Datum"]["after_2026_10_31"], 1)
        self.assertEqual(d["Gemeldet"]["equal_1970_01_01"], 1)
        self.assertEqual(d["reported_before_event"], 2)  # rows b and c

    def test_classification_variants_and_counts(self):
        rep = self.run_qa(line(Fallnummer="a", Klassifikation_Hendry="IFo"),
                          line(Fallnummer="b", Klassifikation_Hendry="S"),
                          line(Fallnummer="c", Klassifikation_Hendry="Hoax"),
                          line(Fallnummer="d", Klassifikation_Hendry="GOOD UFO"),
                          line(Fallnummer="e"))
        c = rep["classification"]
        self.assertEqual((c["hendry_ifo_case_variants_rows"], c["hendry_S_rows"], c["hendry_hoax_rows"],
                          c["hendry_good_ufo_rows"]), (1, 1, 1, 1))
        self.assertEqual(c["Klassifikation_Hendry"]["groups_with_spelling_variants"], 1)

    def test_geography(self):
        rep = self.run_qa(line(Fallnummer="a", PLZ="0"), line(Fallnummer="b", PLZ="407280"),
                          line(Fallnummer="c", PLZ="1A2"), line(Fallnummer="d", Land="deutschland"))
        g = rep["geography"]
        self.assertEqual(g["PLZ"]["equal_to_zero"], 1)
        self.assertEqual(g["PLZ"]["not_digits_only"], 1)
        self.assertEqual(g["Land"]["groups_with_spelling_variants"], 1)
        self.assertEqual(rep["summary"]["rule_counts"]["plz_nonnumeric_or_over_5_digits"], 2)

    def test_privacy_patterns_count_rows_only(self):
        rep = self.run_qa(line(Fallnummer="a", Sachverhalt="mail a@b.de"),
                          line(Fallnummer="b", Sachverhalt="Frau Meier sah es"),
                          line(Fallnummer="c", Sachverhalt="Hauptstraße 12 Licht"),
                          line(Fallnummer="d", Sichtungsort="Dorf Hauptstraße 3"))
        m = rep["privacy_screen"]["Sachverhalt"]["rows_matching"]
        self.assertEqual((m["email"], m["salutation_name"], m["street_with_number"]), (1, 1, 1))
        self.assertEqual(rep["privacy_screen"]["Sichtungsort"]["rows_matching"]["street_suffix"], 1)
        self.assertEqual(rep["summary"]["rule_counts"]["sichtungsort_street_with_number"], 1)

    def test_missing_and_duplicate_ids(self):
        rep = self.run_qa(line(Fallnummer=""), line(Fallnummer="x"), line(Fallnummer="x"))
        self.assertEqual(rep["identifier"]["missing"], 1)
        self.assertEqual(rep["identifier"]["duplicated_values"], 1)

    def test_invalid_time(self):
        rep = self.run_qa(line(**{"lokale Uhrzeit": "25:99"}))
        self.assertEqual(rep["dates"]["lokale Uhrzeit"]["invalid_hh_mm"], 1)

    def test_missing_expected_columns_reported(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.csv"
            p.write_text("a;b\n1;2\n", encoding="utf-8")
            self.assertIn("Land", analyse(p)["input"]["missing_expected_columns"])


if __name__ == "__main__":
    unittest.main()


class DateErrorTypes(unittest.TestCase):
    def test_classification(self):
        from ufo_pipeline.gep_qa import classify_date_value as c
        cases = {"2020": "year_only", "2020-05": "year_month_only", "05.2020": "year_month_only",
                 "2020-13": "year_month_invalid_month", "31.02.2020": "day_invalid_for_month",
                 "2020-13-01": "month_out_of_range", "01.01.20": "two_digit_year",
                 "ca. 2020": "contains_text", "2020-01-01 21:30": "date_with_time_unrecognised_format",
                 "01.01.2020 - 03.01.2020": "range_or_several_dates", "1850-01-01": "year_implausible",
                 "???": "other_shape"}
        for value, expected in cases.items():
            self.assertEqual(c(value), expected, value)

    def test_report_has_no_values(self):
        rep = QA().run_qa(line(Fallnummer="a", Datum="SECRETWORD 2020"), line(Fallnummer="b", Datum="2020"))
        b = rep["dates"]["Datum_unparsable_breakdown"]
        self.assertEqual(b["count"], 2)
        self.assertEqual(b["by_error_type"], {"contains_text": 1, "year_only": 1})
        self.assertNotIn("SECRETWORD", json.dumps(rep))


class PrecisionRules(unittest.TestCase):
    def test_partial_dates_never_get_a_delay(self):
        rep = QA().run_qa(line(Fallnummer="a", Datum="2020", Gemeldet="2021-06-01"),
                          line(Fallnummer="b", Datum="05.2020", Gemeldet="2019-01-01"),
                          line(Fallnummer="c", Datum="2020-01-01", Gemeldet="2022-06-01"),
                          line(Fallnummer="d", Datum="", Gemeldet="2020-01-01"))
        d = rep["dates"]
        self.assertEqual(d["Datum_precision"], {"full": 1, "year_month": 1, "year_only": 1,
                                                "other_unparsable": 0, "missing": 1})
        self.assertTrue(d["Datum_precision_sums_to_rows"])
        self.assertEqual(d["both_dates_present"], 1)           # only the full-precision row
        self.assertEqual(d["reported_before_event"], 0)         # row b (2019 < 05.2020) is NOT counted
        self.assertEqual(d["reported_more_than_1_year_after_event"], 1)
        self.assertEqual(d["gemeldet_present_by_datum_precision"],
                         {"full": 1, "year_month": 1, "year_only": 1, "other_unparsable": 0, "missing": 1})


class AgeHeuristic(unittest.TestCase):
    def ages(self, text):
        from ufo_pipeline.gep_qa import age_findings
        return age_findings(text)

    def test_person_bound_forms(self):
        self.assertEqual(self.ages("Die 45-jährige Frau sah ein Licht"), [45])
        self.assertEqual(self.ages("ein 12 jähriger Junge"), [12])
        self.assertEqual(self.ages("Zeuge, 63 Jahre alt, meldete"), [63])
        self.assertEqual(self.ages("Zeugin (34) sah"), [34])
        self.assertEqual(self.ages("Zeuge (34 J.) sah"), [34])   # matched by two forms, counted once
        self.assertEqual(self.ages("Frau (30) und Mann (41)"), [30, 41])

    def test_objects_and_time_spans_are_not_person_ages(self):
        for text in ("Eine 20-jährige Erfahrung des Piloten", "vor 20 Jahren gab es einen Fall",
                     "das Haus ist 100 Jahre alt", "seit 10 Jahren"):
            self.assertEqual(self.ages(text), [], text)

    def test_report_counts_broad_vs_strict_without_text(self):
        secret = "VERTRAULICH"
        rep = QA().run_qa(line(Fallnummer="a", Sachverhalt=f"{secret} 45-jährige Frau und Zeuge (15)"),
                          line(Fallnummer="b", Sachverhalt="das Haus ist 90 Jahre alt"),
                          line(Fallnummer="c", Sachverhalt="nichts"))
        a = rep["privacy_screen"]["age_analysis"]
        self.assertEqual((a["rows_strict"], a["rows_broad"], a["rows_broad_not_strict"]), (1, 2, 1))
        self.assertEqual(a["rows_with_more_than_one_strict_age"], 1)
        self.assertEqual(a["strict_age_values"]["under_18"], 1)
        self.assertEqual(rep["summary"]["rule_counts"]["sachverhalt_person_age_strict"], 1)
        self.assertEqual(rep["summary"]["rule_counts"]["sachverhalt_age_phrase_unclassified"], 1)
        self.assertNotIn(secret, json.dumps(rep))

    def test_evidence_levels_present(self):
        rep = QA().run_qa(line())
        for k in ("facts", "heuristic_hits", "unverified_assumptions"):
            self.assertTrue(rep["evidence_levels"][k])


class GeographyDeep(unittest.TestCase):
    def geo(self, *rows):
        return QA().run_qa(*[line(Fallnummer=str(i), **kw) for i, kw in enumerate(rows)])

    def test_plz_formats_and_expected_by_country(self):
        rep = self.geo(dict(Land="Deutschland", PLZ="80331"), dict(Land="Deutschland", PLZ="1067"),
                       dict(Land="Deutschland", PLZ="01067"), dict(Land="Schweiz", PLZ="8000"),
                       dict(Land="Schweiz", PLZ="80000"), dict(Land="Vereinigtes Königreich", PLZ="SW1A 1AA"),
                       dict(Land="Deutschland", PLZ="00123"))
        g = rep["geography_deep"]["PLZ"]
        de = g["expected_format_by_country"]["Deutschland"]
        self.assertEqual((de["filled"], de["not_expected_format"], de["shorter_all_digits"]), (4, 1, 1))
        self.assertEqual(g["expected_format_by_country"]["Schweiz"]["longer_or_non_digit"], 1)
        self.assertEqual(g["germany_five_digit_starting_00"], 1)
        self.assertEqual(g["leading_zero_preserved_count"], 2)
        self.assertEqual(g["non_digit_character_kinds_in_values"], {"letter": 1, "space": 1})
        self.assertEqual(rep["summary"]["rule_counts"]["plz_format_unexpected_for_land"], 2)

    def test_placeholders_zero_and_repeated(self):
        rep = self.geo(dict(PLZ="0"), dict(PLZ="99999"), dict(PLZ="xxxxx"), dict(PLZ="?"), dict(Land="x"),
                       dict(Land="Indischer Ozean"))
        g = rep["geography_deep"]
        self.assertEqual(g["PLZ"]["all_zero_values"], 1)
        self.assertEqual(g["PLZ"]["repeated_single_digit_values"], 1)
        self.assertEqual(g["PLZ"]["placeholder_values"], 2)
        self.assertEqual(g["Land"]["placeholder_values"], 1)
        self.assertEqual(g["Land"]["non_country_marker"], 1)
        self.assertEqual(rep["summary"]["rule_counts"]["land_placeholder"], 1)
        self.assertEqual(rep["summary"]["rule_counts"]["land_not_a_country"], 1)

    def test_plz_unvollstaendig_groups_and_padding(self):
        rep = self.geo(dict(PLZ="", PLZ_unvollstaendig="80"), dict(PLZ="", PLZ_unvollstaendig="803"),
                       dict(PLZ="", PLZ_unvollstaendig="8033"), dict(PLZ="", PLZ_unvollstaendig="65580000"),
                       dict(PLZ="", PLZ_unvollstaendig="12(3)"), dict(PLZ="80331", PLZ_unvollstaendig="80"))
        u = rep["geography_deep"]["PLZ_unvollstaendig"]
        self.assertEqual(u["groups"]["1_to_3_digits"], 3)
        self.assertEqual(u["groups"]["4_or_more_digits"], 2)
        self.assertEqual(u["groups"]["6_or_more_digits"], 1)
        self.assertEqual(u["values_ending_with_zero_padding"]["ends_with_0000"], 1)
        self.assertEqual(u["non_digit_character_kinds_in_values"],
                         {"close_parenthesis": 1, "open_parenthesis": 1})
        self.assertEqual(u["overlap_with_plz"], 1)
        self.assertIn("UNVERIFIED", u["meaning_status"])
        b = u["reading_bands"]
        self.assertEqual((b["1_to_3_digits"], b["4_digits"], b["5_digits"], b["6_or_more_digits"]),
                         (3, 1, 0, 1))
        self.assertEqual(b["4_digits_in_land_with_4_digit_postcodes"], 0)
        self.assertEqual(rep["summary"]["rule_counts"]["plz_unvollstaendig_4_or_more_digits"], 2)

    def test_small_cells_and_no_codes_in_report(self):
        rep = self.geo(dict(PLZ="12345"), dict(PLZ="12345"), dict(PLZ="54321"))
        c = rep["geography_deep"]["PLZ"]["small_cells"]
        self.assertEqual((c["distinct_plz"], c["rows_in_plz_with_exactly_1_row"],
                          c["rows_in_plz_with_fewer_than_3_rows"]), (2, 1, 3))
        dump = json.dumps(rep)
        for code in ("12345", "54321"):
            self.assertNotIn(code, dump)

    def test_bundesland_checks(self):
        rep = self.geo(dict(Bundesland="", Land="Deutschland"), dict(Bundesland="Bayern", Land=""),
                       dict(Bundesland="?", Land="Österreich"))
        b = rep["geography_deep"]["Bundesland"]
        self.assertEqual((b["missing_for_germany"], b["filled_but_land_missing_or_placeholder"],
                          b["placeholder_values"]), (1, 1, 1))


    def test_four_digit_value_in_swiss_row_is_separated(self):
        rep = self.geo(dict(Land="Schweiz", PLZ="", PLZ_unvollstaendig="8000"),
                       dict(Land="Deutschland", PLZ="", PLZ_unvollstaendig="8033"))
        b = rep["geography_deep"]["PLZ_unvollstaendig"]["reading_bands"]
        self.assertEqual((b["4_digits"], b["4_digits_in_land_with_4_digit_postcodes"]), (2, 1))


class ReidentificationRisk(unittest.TestCase):
    def rep(self, rows):
        return QA().run_qa(*[line(Fallnummer=f"id{i}", **kw) for i, kw in enumerate(rows)])["reidentification_risk"]

    def person(self, age, plz="80331", datum="2020-01-01", ort="Ortsname", **kw):
        return dict(Sachverhalt=f"Die {age}-jährige Frau sah es", PLZ=plz, Datum=datum, Sichtungsort=ort, **kw)

    def test_groups_smaller_than_5_are_merged_not_listed(self):
        rows = [self.person(40) for _ in range(6)] + [self.person(41, plz="10115"), self.person(42, plz="20095"),
                                                      self.person(43, plz="30159")]
        r = self.rep(rows)["strict_age_cohort"]["L3_plz_age_date"]
        self.assertEqual(r["eligible_rows"], 9)
        self.assertEqual(r["rows_in_groups_smaller_than_5"], "<5")           # 3 unique rows -> suppressed
        self.assertEqual(r["share_in_groups_smaller_than_5"], round(3 / 9, 3))
        self.assertEqual(r["risk_indicator"], "high")   # 33% is in the 25-75% band

    def test_no_keys_codes_or_place_names_in_output(self):
        rows = [self.person(40 + i, plz=f"7{i}123", ort=f"Geheimort{i}") for i in range(8)]
        dump = json.dumps(self.rep(rows))
        for needle in ("Geheimort", "7012", "71123", "80331", "2020-01-01", "id0"):
            self.assertNotIn(needle, dump)

    def test_reduced_or_missing_dates_never_enter_date_levels(self):
        rows = ([self.person(40, datum="2020") for _ in range(5)] + [self.person(40, datum="05.2020")] * 5
                + [self.person(40, datum="") for _ in range(5)] + [self.person(40) for _ in range(5)])
        r = self.rep(rows)["strict_age_cohort"]
        self.assertEqual(r["L2_plz_age"]["eligible_rows"], 20)
        self.assertEqual(r["L3_plz_age_date"]["eligible_rows"], 5)          # only full dates
        self.assertEqual(r["L3_plz_age_date"]["rows_excluded_missing_component_or_reduced_date"], 15)

    def test_strict_and_unclassified_cohorts_are_separate(self):
        rows = [self.person(40) for _ in range(6)] + [dict(Sachverhalt="das Haus ist 90 Jahre alt", PLZ="80331",
                                                           Datum="2020-01-01") for _ in range(6)]
        r = self.rep(rows)
        self.assertEqual(r["cohorts"]["strict_person_bound_age"], 6)
        self.assertEqual(r["cohorts"]["unclassified_age_phrase_not_verified"], 6)
        self.assertEqual(r["unclassified_cohort_not_verified"]["L2_plz_age"]["eligible_rows"], 6)
        self.assertEqual(r["strict_age_cohort"]["L2_plz_age"]["eligible_rows"], 6)

    def test_minors_use_stricter_threshold_and_banded_share(self):
        adults = [self.person(40) for _ in range(6)]
        few_minors = [self.person(12, plz=f"1000{i}") for i in range(7)]
        r = self.rep(adults + few_minors)
        self.assertEqual(r["cohorts"]["minors_any_strict_age_under_18"], "<10")
        m = r["minors_strict_cohort_banded_and_suppressed"]["L2_plz_age"]
        self.assertEqual(m["eligible_rows"], "<10")
        self.assertNotIn("share_in_groups_smaller_than_5", m)
        many = self.rep([self.person(12, plz=f"1{i:04d}") for i in range(12)])
        m = many["minors_strict_cohort_banded_and_suppressed"]["L2_plz_age"]
        self.assertEqual(m["eligible_rows"], 12)
        self.assertEqual(m["share_in_groups_smaller_than_5_band"], "75%+")
        self.assertNotIn("share_in_groups_smaller_than_5", m)
        self.assertNotIn("risk_indicator", m)

    def test_small_cohort_is_suppressed(self):
        r = self.rep([self.person(40) for _ in range(3)])["strict_age_cohort"]["L2_plz_age"]
        self.assertEqual(r["status"], "suppressed: too few rows")
        self.assertEqual(r["eligible_rows"], "<5")

    def test_generalised_level_reduces_uniqueness(self):
        rows = [self.person(40 + i % 3, plz=f"80{i % 3}{i % 7}1", datum=f"2020-01-{i % 9 + 1:02d}")
                for i in range(30)]
        r = self.rep(rows)["strict_age_cohort"]
        self.assertGreaterEqual(r["L3_plz_age_date"]["share_in_groups_smaller_than_5"],
                                r["L6_generalised_plz2_ageband_month"]["share_in_groups_smaller_than_5"])


    def test_coarse_level_is_not_riskier_than_fine_level(self):
        rows = [self.person(40 + i % 3, plz=f"80{i % 3}{i % 7}1", datum=f"2020-01-{i % 9 + 1:02d}")
                for i in range(30)]
        r = self.rep(rows)["strict_age_cohort"]
        self.assertGreaterEqual(r["L6_generalised_plz2_ageband_month"]["share_in_groups_smaller_than_5"],
                                r["L7_coarse_plz1_ageband_decade"]["share_in_groups_smaller_than_5"])
