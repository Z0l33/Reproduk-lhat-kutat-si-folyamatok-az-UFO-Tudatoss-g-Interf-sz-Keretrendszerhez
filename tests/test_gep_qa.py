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
