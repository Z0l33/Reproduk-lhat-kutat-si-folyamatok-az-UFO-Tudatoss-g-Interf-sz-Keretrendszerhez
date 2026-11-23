"""Fixture builders.

IMPORTANT: `make_logic_fixture` re-labels SYNTHETIC data as if it were a verified, non-synthetic
dataset so that the confirmatory / replication LOGIC can be tested end to end. Such fixtures are
fake, live only in temporary directories, and are never evidence of anything.
"""
import copy
import tempfile
from pathlib import Path

from ufo_pipeline.prereg import prereg_hash
from ufo_pipeline.provenance import load_json, write_json
from ufo_pipeline.synthetic import write_dataset

ANALYSIS = {"group_column": "group", "group_a": "A", "group_b": "B", "outcome_column": "outcome",
            "expected_direction": "a>b", "alpha": 0.05, "sesoi_g": 0.2, "n_permutations": 1000,
            "n_bootstrap": 500, "seed": 1, "require_replication": True}
VALIDATION = {"columns": {"subject_id": {"type": "str"}, "group": {"type": "str"},
                          "outcome": {"type": "float"}, "covariate": {"type": "float"}},
              "id_column": "subject_id", "min_group_size": 10}


def make_prereg(dataset_id, *, registered_at="2029-01-01T00:00:00+00:00",
                registry_id="SYNTHETIC-FIXTURE-REGISTRY", **analysis_over):
    return {"prereg_id": f"prereg-{dataset_id}", "registered_at": registered_at,
            "registry_id": registry_id, "hypothesis": "fixture hypothesis (fake)",
            "dataset_id": dataset_id, "analysis": {**ANALYSIS, **analysis_over}}


def write_policy(root, dataset_id, policy="auto"):
    """policy: 'auto' = explicit empty-denylist decision for this dataset; 'repo' = copy the repo's real
    policy; None = no policy file; a dict = use as given."""
    import json
    import shutil
    target = Path(root) / "policies" / "sensitive_columns.json"
    if policy is None:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if policy == "repo":
        shutil.copy(Path(__file__).resolve().parents[1] / "policies" / "sensitive_columns.json", target)
    elif policy == "auto":
        target.write_text(json.dumps({"schema_version": 1, "global_denied_column_patterns": [],
                                      "datasets": {dataset_id: {"denied_columns": []}}}), encoding="utf-8")
    else:
        target.write_text(json.dumps(policy), encoding="utf-8")


def rewrite_column(paths, name, fn):
    """Replace every value of an existing column with fn(old_value) and refresh the provenance hash."""
    import csv
    from ufo_pipeline.hashing import sha256_file
    csv_path = Path(paths["csv"])
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    idx = rows[0].index(name)
    for r in rows[1:]:
        r[idx] = fn(r[idx])
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    prov = load_json(paths["provenance"])
    for f in prov["files"]:
        if f["name"] == csv_path.name:
            f["sha256"], f["size"] = sha256_file(csv_path), csv_path.stat().st_size
    write_json(paths["provenance"], prov)


def add_column(paths, name, value_fn):
    """Append a column to a synthetic CSV and refresh the recorded size and SHA-256 in its provenance."""
    import csv
    from ufo_pipeline.hashing import sha256_file
    csv_path = Path(paths["csv"])
    with open(csv_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    rows[0].append(name)
    for i, r in enumerate(rows[1:]):
        r.append(value_fn(i))
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    prov = load_json(paths["provenance"])
    for f in prov["files"]:
        if f["name"] == csv_path.name:
            f["sha256"], f["size"] = sha256_file(csv_path), csv_path.stat().st_size
    write_json(paths["provenance"], prov)


def make_logic_fixture(scenario, *, dataset_id, seed=0, status="verified", file_status="verified",
                       prereg=True, prereg_over=None, analysis_over=None, extra_prereg=None,
                       n_per_group=120, root=None, record_id="0", policy="auto", explicit_allowlist=True):
    root = Path(root or tempfile.mkdtemp())
    paths = write_dataset(scenario, root, n_per_group=n_per_group, seed=seed)
    prov = load_json(paths["provenance"])
    prov.update({"dataset_id": dataset_id, "synthetic": False, "source": "zenodo",
                 "record_id": record_id, "doi": f"10.5281/zenodo.{record_id}",
                 "status": status, "first_retrieved_at": "2030-01-01T00:00:00+00:00"})
    for f in prov["files"]:
        f["status"] = file_status
    write_json(paths["provenance"], prov)
    analysis = {**copy.deepcopy(ANALYSIS), **(analysis_over or {})}
    cfg = {"analysis_id": f"fixture-{dataset_id}", "dataset_provenance": paths["provenance"].name,
           "data_file": paths["csv"].name, "validation": copy.deepcopy(VALIDATION),
           "leakage": {"feature_columns": ["covariate"]}, "analysis": analysis}
    if explicit_allowlist:
        from ufo_pipeline.access import referenced_columns
        cfg["data_access"] = {"allowed_columns": sorted(referenced_columns(cfg))}
    write_policy(root, dataset_id, policy)
    prereg_doc = None
    if prereg:
        prereg_doc = make_prereg(dataset_id, **(prereg_over or {}))
        if extra_prereg:
            prereg_doc.update(extra_prereg)
        write_json(root / "prereg.json", prereg_doc)
        cfg["preregistration"] = "prereg.json"
    return {"root": root, "config": cfg, "prereg": prereg_doc,
            "prereg_hash": prereg_hash(prereg_doc) if prereg_doc else None, "paths": paths}
