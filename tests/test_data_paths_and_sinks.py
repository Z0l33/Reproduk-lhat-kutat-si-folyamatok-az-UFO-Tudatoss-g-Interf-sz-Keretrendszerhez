"""Data-path inventory, output sinks and concurrency.

SCOPE OF THE ASSURANCE (read before relying on these tests): they show, for the code paths that exist in
this repository and the inputs used here, that denied columns are not loaded by the analysis layer and that
tested outputs (report files, stdout, stderr, exception text) contain no canary values. They do not prove
that no other process, a modified copy of the code, a debugger, a swap file or an environment outside these
tests can read the file. The reader inventory below is a tripwire for unplanned reading code, not a proof.
"""
import ast
import io
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from ufo_pipeline.cli import build_parser, main
from ufo_pipeline.gep_qa import analyse
from ufo_pipeline.pipeline import run_analysis
from ufo_pipeline.provenance import write_json
from tests.helpers import add_column, make_logic_fixture, rewrite_column

PKG = Path(__file__).resolve().parents[1] / "ufo_pipeline"
CANARY = "KANARI-9f3a"

# Modules that may read files or sockets, and why. Anything new must be registered here consciously.
READERS = {
    "access.py": "policy JSON + the ONLY analysis data loader (allowlisted columns)",
    "gep_qa.py": "audit tool for the data custodian: reads the whole file, emits aggregates only",
    "profile.py": "audit tool: structural profile, withholds values of denied columns",
    "hashing.py": "binary hashing",
    "provenance.py": "JSON provenance records",
    "synthetic.py": "writes synthetic test data",
    "zenodo.py": "network download of public records",
    "notion_sync.py": "network (Notion API) rows",
}
CSV_IMPORTERS = {"access.py", "gep_qa.py", "profile.py", "synthetic.py"}
READ_CALLS = {"open", "read_text", "read_bytes", "urlopen", "loadtxt", "genfromtxt", "read_csv"}


def _calls_and_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls, imports = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name:
                calls.add(name)
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "csv":
                    calls.add("csv." + f.attr)
        elif isinstance(node, ast.Import):
            imports |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    return calls, imports


class ReaderInventory(unittest.TestCase):
    def test_only_registered_modules_read_data(self):
        found = set()
        for path in sorted(PKG.glob("*.py")):
            calls, _ = _calls_and_imports(path)
            if calls & READ_CALLS or calls & {"csv.reader", "csv.DictReader"}:
                found.add(path.name)
        self.assertEqual(found, set(READERS), "a module reads files/network that is not registered in READERS")

    def test_csv_parsing_is_confined(self):
        for path in sorted(PKG.glob("*.py")):
            _, imports = _calls_and_imports(path)
            if "csv" in imports:
                self.assertIn(path.name, CSV_IMPORTERS, path.name)

    def test_analysis_modules_cannot_read_files(self):
        for name in ("pipeline.py", "validation.py", "stats.py", "evidence.py"):
            calls, imports = _calls_and_imports(PKG / name)
            self.assertFalse(calls & (READ_CALLS | {"csv.reader", "csv.DictReader"}), name)
            self.assertNotIn("csv", imports, name)

    def test_unrestricted_loader_is_gone_and_pipeline_uses_the_allowlisted_one(self):
        import ufo_pipeline.validation as validation
        self.assertFalse(hasattr(validation, "load_csv"))
        calls, _ = _calls_and_imports(PKG / "pipeline.py")
        self.assertIn("load_csv_allowlisted", calls)
        self.assertIn("resolve_access", calls)

    def test_only_analyze_takes_a_config_and_it_enforces_the_policy(self):
        sub = next(a for a in build_parser()._actions if a.dest == "command")
        with_config = {name for name, p in sub.choices.items()
                       if any("--config" in a.option_strings for a in p._actions)}
        self.assertEqual(with_config, {"analyze"})
        fx = make_logic_fixture("null", dataset_id="x-real")
        fx["config"]["analysis"]["outcome_column"] = "email"
        cfg = fx["root"] / "cfg.json"
        write_json(cfg, fx["config"])
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["analyze", "--config", str(cfg), "--out", str(fx["root"] / "o"),
                         "--base-dir", str(fx["root"])])
        self.assertEqual(code, 2)
        self.assertFalse((fx["root"] / "o").exists())


class OutputSinks(unittest.TestCase):
    def test_record_ids_never_reach_reports_stdout_stderr_or_exceptions(self):
        fx = make_logic_fixture("id_overlap", dataset_id="x-real")
        rewrite_column(fx["paths"], "subject_id", lambda v: f"{CANARY}-{v}")
        cfg = fx["root"] / "cfg.json"
        write_json(cfg, fx["config"])
        out = fx["root"] / "out"
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            code = main(["analyze", "--config", str(cfg), "--out", str(out), "--base-dir", str(fx["root"])])
        self.assertEqual(code, 0)
        report = json.loads(next(out.glob("*.json")).read_text(encoding="utf-8"))
        self.assertTrue(report["leakage"]["leakage_detected"])           # the finding is still reported...
        self.assertTrue(all("examples" not in f for f in report["leakage"]["findings"]))   # ...without ids
        texts = [buf_out.getvalue(), buf_err.getvalue()] + [p.read_text(encoding="utf-8")
                                                            for p in out.rglob("*") if p.is_file()]
        for text in texts:
            self.assertNotIn(CANARY, text)

    def test_synthetic_data_may_show_examples(self):
        from ufo_pipeline.synthetic import write_dataset
        from tests.helpers import ANALYSIS, VALIDATION
        import copy
        paths = write_dataset("id_overlap", Path.cwd(), n_per_group=40, seed=1)
        cfg = {"analysis_id": "syn", "dataset_provenance": paths["provenance"].name,
               "data_file": paths["csv"].name, "validation": copy.deepcopy(VALIDATION),
               "analysis": copy.deepcopy(ANALYSIS)}
        rep = run_analysis(cfg, base_dir=Path.cwd())
        self.assertTrue(any("examples" in f for f in rep["leakage"]["findings"]))

    def test_profile_withholds_values_of_denied_columns(self):
        d = Path.cwd()
        (d / "x.csv").write_text("PLZ;Zeitzone;Sichtungsort;Alter\n" +
                                 "".join(f"{CANARY}-1;MEZ;{CANARY}-2;{i}\n" for i in range(6)), encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["profile", str(d / "x.csv"), "--base-dir", str(d)]), 0)   # no policy file
        text = out.getvalue()
        self.assertNotIn(CANARY, text)
        self.assertIn("MEZ", text)                                  # non-sensitive category still shown
        self.assertEqual(sum('"values_withheld": true' in t for t in text.split("}")), 2)

    def test_profile_uses_policy_denied_columns_of_any_dataset(self):
        d = Path.cwd()
        (d / "policies").mkdir()
        (d / "policies" / "sensitive_columns.json").write_text(json.dumps(
            {"schema_version": 1, "datasets": {"x": {"denied_columns": ["Geheim"]}}}), encoding="utf-8")
        (d / "x.csv").write_text("Geheim;Offen\n" + "".join(f"{CANARY};ja\n" for _ in range(4)), encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            main(["profile", str(d / "x.csv"), "--base-dir", str(d)])
        self.assertNotIn(CANARY, out.getvalue())
        self.assertIn("ja", out.getvalue())

    def test_gep_qa_output_has_no_cell_values_of_any_column(self):
        header = ("Gemeldet;Fallnummer;Datum;Land;Bundesland;PLZ;PLZ_unvollstaendig;Sichtungsort;"
                  "lokale Uhrzeit;Zeitzone;Tageszeit;UTC;Erstkontakt;Sachverhalt;Klassifikation_Hynek;"
                  "Klassifikation_Hendry;Klassifikation_IFO-Verifikationsschluessel;Identifikation;"
                  "Identifikations_Gruppe")
        cols = header.split(";")
        rows = []
        for i in range(8):
            r = {c: "" for c in cols}
            r.update(Fallnummer=f"{CANARY}-{i}", Datum="2020-01-01", Land="Deutschland",
                     PLZ=f"{CANARY}", PLZ_unvollstaendig=f"{CANARY}", Sichtungsort=f"{CANARY}-ort",
                     Sachverhalt=f"{CANARY} 45-jährige Frau", Identifikation=f"{CANARY}-ident",
                     Identifikations_Gruppe=f"{CANARY}-grp", Bundesland=f"{CANARY}-bl")
            rows.append(";".join(r[c] for c in cols))
        path = Path.cwd() / "g.csv"
        path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
        self.assertNotIn(CANARY, json.dumps(analyse(path), ensure_ascii=False))


class Concurrency(unittest.TestCase):
    def test_parallel_analyses_do_not_influence_each_other(self):
        def one(seed):
            fx = make_logic_fixture("true_effect", dataset_id=f"x-real-{seed}", seed=seed, record_id=str(seed))
            rep = run_analysis(fx["config"], base_dir=fx["root"])
            return seed, rep["analysis"]["hedges_g"], rep["assessment"]["status"], rep["data_access"]["allowed_columns"]

        seeds = list(range(8))
        sequential = [one(s) for s in seeds]
        with ThreadPoolExecutor(max_workers=4) as pool:
            parallel = list(pool.map(one, seeds))
        self.assertEqual(sequential, parallel)

    def test_denied_reference_in_one_thread_does_not_affect_others(self):
        def one(i):
            fx = make_logic_fixture("null", dataset_id=f"x-real-{i}", record_id=str(i))
            if i % 2:
                fx["config"]["analysis"]["outcome_column"] = "PLZ"
            try:
                run_analysis(fx["config"], base_dir=fx["root"])
                return "ran"
            except Exception as exc:  # noqa: BLE001
                return type(exc).__name__

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(one, range(8)))
        self.assertEqual(results, ["ran", "ColumnAccessError"] * 4)

    def test_tests_do_not_leak_into_the_working_directory(self):
        self.assertEqual(os.listdir(Path.cwd()), [])   # conftest gives every test an empty cwd


if __name__ == "__main__":
    unittest.main()
