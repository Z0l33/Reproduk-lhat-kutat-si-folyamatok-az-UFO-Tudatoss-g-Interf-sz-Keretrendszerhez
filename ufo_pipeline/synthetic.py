"""Synthetic test datasets with KNOWN ground truth.

Everything produced here is flagged `synthetic: true` in its provenance record and can never
count as evidence for any hypothesis. It exists only to test the pipeline's logic.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Union

import numpy as np

from .hashing import sha256_file
from .provenance import PROVENANCE_SCHEMA_VERSION, utc_now_iso, write_json
from . import __version__

SCENARIOS = ("true_effect", "null", "below_sesoi", "leaky_proxy", "id_overlap")
FIELDS = ["subject_id", "group", "outcome", "covariate"]
PathLike = Union[str, Path]


def make_rows(scenario: str, *, n_per_group: int = 120, seed: int = 0) -> List[Dict[str, Any]]:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; choose from {SCENARIOS}")
    rng = np.random.default_rng(seed)
    shift = {"true_effect": 0.8, "null": 0.0, "below_sesoi": 0.05}.get(scenario, 0.8)
    if scenario == "below_sesoi":
        n_per_group = max(n_per_group, 4000)  # large n -> CI narrow enough to sit inside SESOI
    a = rng.normal(shift, 1.0, n_per_group)
    b = rng.normal(0.0, 1.0, n_per_group)
    rows: List[Dict[str, Any]] = []
    for i, value in enumerate(a):
        rows.append({"subject_id": f"S{i:05d}", "group": "A", "outcome": round(float(value), 6)})
    for i, value in enumerate(b):
        rows.append({"subject_id": f"S{n_per_group + i:05d}", "group": "B", "outcome": round(float(value), 6)})
    for row in rows:
        row["covariate"] = round(float(rng.normal(0, 1)), 6)
    if scenario == "leaky_proxy":
        for row in rows:
            row["covariate"] = round(row["outcome"] * 2 + float(rng.normal(0, 0.001)), 6)
    if scenario == "id_overlap":
        for row in rows[n_per_group:n_per_group + 10]:  # first 10 B subjects reuse A ids
            row["subject_id"] = f"S{rows.index(row) - n_per_group:05d}"
    return rows


def write_dataset(scenario: str, out_dir: PathLike, *, n_per_group: int = 120, seed: int = 0) -> Dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"synthetic_{scenario}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(make_rows(scenario, n_per_group=n_per_group, seed=seed))
    now = utc_now_iso()
    prov = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "dataset_id": f"synthetic-{scenario.replace('_', '-')}",
        "source": "synthetic-generator", "synthetic": True, "status": "synthetic",
        "retrieved_at": now, "first_retrieved_at": now,
        "tool": {"name": "ufo-pipeline", "version": __version__},
        "generator": {"scenario": scenario, "n_per_group": n_per_group, "seed": seed},
        "files": [{"name": csv_path.name, "size": csv_path.stat().st_size,
                   "sha256": sha256_file(csv_path), "status": "synthetic"}],
        "errors": [],
    }
    prov_path = out_dir / f"synthetic_{scenario}.provenance.json"
    write_json(prov_path, prov)
    return {"csv": csv_path, "provenance": prov_path}
