"""Fixture builders.

IMPORTANT: `make_logic_fixture` re-labels SYNTHETIC data as if it were a verified, non-synthetic
dataset so that the confirmatory / replication LOGIC can be tested end to end. Such fixtures are
fake, live only in temporary directories, and are never evidence of anything.
"""
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


def make_logic_fixture(scenario, *, dataset_id, seed=0, status="verified", file_status="verified",
                       prereg=True, prereg_over=None, analysis_over=None, extra_prereg=None,
                       n_per_group=120, root=None, record_id="0"):
    root = Path(root or tempfile.mkdtemp())
    paths = write_dataset(scenario, root, n_per_group=n_per_group, seed=seed)
    prov = load_json(paths["provenance"])
    prov.update({"dataset_id": dataset_id, "synthetic": False, "source": "zenodo",
                 "record_id": record_id, "doi": f"10.5281/zenodo.{record_id}",
                 "status": status, "first_retrieved_at": "2030-01-01T00:00:00+00:00"})
    for f in prov["files"]:
        f["status"] = file_status
    write_json(paths["provenance"], prov)
    analysis = {**ANALYSIS, **(analysis_over or {})}
    cfg = {"analysis_id": f"fixture-{dataset_id}", "dataset_provenance": paths["provenance"].name,
           "data_file": paths["csv"].name, "validation": VALIDATION,
           "leakage": {"feature_columns": ["covariate"]}, "analysis": analysis}
    prereg_doc = None
    if prereg:
        prereg_doc = make_prereg(dataset_id, **(prereg_over or {}))
        if extra_prereg:
            prereg_doc.update(extra_prereg)
        write_json(root / "prereg.json", prereg_doc)
        cfg["preregistration"] = "prereg.json"
    return {"root": root, "config": cfg, "prereg": prereg_doc,
            "prereg_hash": prereg_hash(prereg_doc) if prereg_doc else None, "paths": paths}
