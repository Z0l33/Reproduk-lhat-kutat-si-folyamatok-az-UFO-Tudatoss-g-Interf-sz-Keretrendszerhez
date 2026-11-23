import tempfile
import unittest
from pathlib import Path

from ufo_pipeline.profile import profile_csv


class ProfileTests(unittest.TestCase):
    def run_on(self, text, encoding="utf-8"):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.csv"
            p.write_bytes(text.encode(encoding))
            return profile_csv(p)

    def test_counts_and_kinds(self):
        r = self.run_on("date;n;shape;note\n2020-01-02;3;disc;a long free text here\n"
                        "2021-05-06;5;disc;other\n2021-05-06;5;disc;other\n")
        self.assertEqual(r["delimiter"], ";")
        self.assertEqual(r["data_rows"], 3)
        self.assertEqual(r["fully_duplicated_rows"], 1)
        kinds = {c["name"]: c for c in r["columns"]}
        self.assertEqual(kinds["date"]["kind"], "date")
        self.assertEqual(kinds["n"]["max"], 5.0)
        self.assertEqual(kinds["shape"]["value_counts"], {"disc": 3})

    def test_long_free_text_never_listed(self):
        r = self.run_on("t\n" + "x" * 100 + "\n" + "y" * 100 + "\n")
        self.assertNotIn("value_counts", r["columns"][0])

    def test_wrong_width_and_empty_rows(self):
        r = self.run_on("a,b\n1,2\n3\n,\n")
        self.assertEqual(r["rows_with_wrong_width"], 1)
        self.assertEqual(r["fully_empty_rows"], 1)

    def test_latin_encoding_detected(self):
        r = self.run_on("név\nÁrpád\n", encoding="cp1252")
        self.assertEqual(r["encoding"], "cp1252")


if __name__ == "__main__":
    unittest.main()
