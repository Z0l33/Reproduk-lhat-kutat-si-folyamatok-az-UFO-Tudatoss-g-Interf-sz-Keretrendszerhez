"""End-to-end analysis: integrity check -> validation -> leakage -> statistics -> evidence class."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from . import __version__
from .evidence import assess, evaluate_replication
from .prereg import check_prereg, load_prereg, prereg_hash
from .provenance import (dataset_is_verified, load_json, require_local_file_integrity,
                         utc_now_iso, validate_provenance, write_json)
from .stats import analyze_two_groups
from .validation import detect_leakage, load_csv, parse_float, validate_schema

PathLike = Union[str, Path]
REPORT_SCHEMA_VERSION = 1
REQUIRED_ANALYSIS_KEYS = ("group_column", "group_a", "group_b", "outcome_column", "alpha",
                          "sesoi_g", "n_permutations", "n_bootstrap", "seed")

BANNERS = {
    "invalid_data": "INVALID DATA - no analysis performed",
    "invalid_leakage": "DATA LEAKAGE DETECTED - no analysis performed, result not interpretable",
    "unverified_data": "DATASET NOT VERIFIED - no analysis performed",
    "synthetic_not_evidence": "SYNTHETIC TEST DATA - never evidence",
    "exploratory_hypothesis_generating": "EXPLORATORY - hypothesis-generating only, NOT confirmatory evidence",
    "confirmatory_null_equivalence": "CONFIRMATORY NULL - effect equivalent to zero within the SESOI",
    "confirmatory_inconclusive": "CONFIRMATORY BUT INCONCLUSIVE - not evidence for the hypothesis",
    "confirmatory_below_sesoi_or_uncertain": "BELOW OR NOT CLEARLY ABOVE SESOI - not positive evidence",
    "confirmatory_wrong_direction": "WRONG DIRECTION vs preregistration - not positive evidence",
    "awaiting_independent_replication": "CONFIRMATORY SIGNAL, AWAITING INDEPENDENT REPLICATION - not yet counted",
    "confirmatory_positive_unreplicated": "CONFIRMATORY POSITIVE (unreplicated)",
    "confirmatory_positive_replicated": "CONFIRMATORY POSITIVE, INDEPENDENTLY REPLICATED",
    "replication_failed": "REPLICATION FAILED - not positive evidence",
    "replication_invalid_not_counted": "REPLICATION INVALID - ignored, not counted",
    "no_analysis": "NO ANALYSIS RESULT",
}


def run_analysis(config: Dict[str, Any], *, base_dir: PathLike = ".") -> Dict[str, Any]:
    base = Path(base_dir)
    params: Dict[str, Any] = dict(config["analysis"])
    for key in REQUIRED_ANALYSIS_KEYS:
        if key not in params:
            raise ValueError(f"analysis config missing key: {key}")
    params.setdefault("expected_direction", "any")
    params.setdefault("require_replication", True)

    prov = load_json(base / config["dataset_provenance"])
    data_path = base / config["data_file"]
    provenance_problems = validate_provenance(prov)
    integrity = require_local_file_integrity(prov, data_path)  # raises IntegrityError on mismatch
    synthetic = prov.get("synthetic") is True
    # Re-hash EVERY file of the dataset locally; a 'verified' status string alone is never trusted.
    verified = dataset_is_verified(prov, data_path.parent)

    columns, rows = load_csv(data_path)
    validation = validate_schema(columns, rows, config.get("validation", {}))
    leak_cfg = config.get("leakage", {})
    leakage = detect_leakage(
        rows, group_column=params["group_column"], outcome_column=params["outcome_column"],
        id_column=config.get("validation", {}).get("id_column"),
        feature_columns=leak_cfg.get("feature_columns", []), split_column=leak_cfg.get("split_column"),
        train_value=leak_cfg.get("train_value", "train"), test_value=leak_cfg.get("test_value", "test"),
        time_column=leak_cfg.get("time_column"), proxy_threshold=leak_cfg.get("proxy_threshold", 0.99))

    prereg_info: Optional[Dict[str, Any]] = None
    prereg_problems: Optional[List[str]] = None
    if config.get("preregistration"):
        prereg = load_prereg(base / config["preregistration"])
        prereg_problems = check_prereg(prereg, params, dataset_id=prov["dataset_id"],
                                       dataset_first_retrieved_at=prov.get("first_retrieved_at"))
        prereg_info = {"path": config["preregistration"], "sha256": prereg_hash(prereg),
                       "prereg_id": prereg.get("prereg_id"), "registry_id": prereg.get("registry_id"),
                       "replicates_prereg_sha256": prereg.get("replicates_prereg_sha256"),
                       "problems": prereg_problems}

    data_ok = validation["valid"] and not leakage["leakage_detected"] and (verified or synthetic)
    analysis: Optional[Dict[str, Any]] = None
    if data_ok:
        g_col, o_col = params["group_column"], params["outcome_column"]
        a = [parse_float(r.get(o_col)) for r in rows if r.get(g_col) == params["group_a"]]
        b = [parse_float(r.get(o_col)) for r in rows if r.get(g_col) == params["group_b"]]
        a, b = [v for v in a if v is not None], [v for v in b if v is not None]
        min_n = int(config.get("validation", {}).get("min_group_size", 2))
        if len(a) < min_n or len(b) < min_n:
            validation["valid"] = False
            validation["errors"].append(f"group_too_small:{len(a)},{len(b)}<{min_n}")
        else:
            analysis = analyze_two_groups(a, b, sesoi=params["sesoi_g"], alpha=params["alpha"],
                                          n_permutations=params["n_permutations"],
                                          n_bootstrap=params["n_bootstrap"], seed=params["seed"])

    inputs = {"data_valid": validation["valid"], "leakage_detected": leakage["leakage_detected"],
              "dataset_verified": verified, "synthetic": synthetic, "prereg_problems": prereg_problems,
              "analysis": analysis, "expected_direction": params["expected_direction"],
              "require_replication": bool(params["require_replication"])}
    assessment = assess(**inputs)

    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "analysis_id": config.get("analysis_id", "analysis"),
        "generated_at": utc_now_iso(),
        "tool_version": __version__,
        "dataset": {"dataset_id": prov["dataset_id"], "record_id": prov.get("record_id"),
                    "doi": prov.get("doi"), "synthetic": synthetic, "provenance_status": prov.get("status"),
                    "provenance_problems": provenance_problems,
                    "file_sha256": {integrity["name"]: integrity["actual"]},
                    "first_retrieved_at": prov.get("first_retrieved_at")},
        "validation": validation, "leakage": leakage, "preregistration": prereg_info,
        "analysis_parameters": params, "analysis": analysis,
        "assessment": assessment.to_dict(), "assessment_inputs": inputs,
    }


def reassess_with_replication(original: Dict[str, Any], evaluation: Dict[str, Any]) -> Dict[str, Any]:
    inputs = dict(original["assessment_inputs"])
    return assess(**inputs, replication_outcome=evaluation["outcome"]).to_dict()


def evaluate_and_reassess(original: Dict[str, Any], replication: Dict[str, Any]) -> Dict[str, Any]:
    evaluation = evaluate_replication(original, replication)
    return {"replication": evaluation, "original_assessment_before": original["assessment"],
            "original_assessment_after": reassess_with_replication(original, evaluation)}


def render_markdown(report: Dict[str, Any]) -> str:
    a = report["assessment"]
    lines = [f"# {report['analysis_id']}", "",
             f"**{BANNERS.get(a['status'], a['status'])}**", "",
             f"- Status: `{a['status']}` ({a['mode']})",
             f"- Counts as positive evidence: **{'YES' if a['counts_as_positive_evidence'] else 'NO'}**",
             f"- Dataset: `{report['dataset']['dataset_id']}` (synthetic: {report['dataset']['synthetic']})", ""]
    if report["analysis"]:
        s = report["analysis"]
        lines += ["## Result", "",
                  f"- Hedges' g = {s['hedges_g']:.3f}, {int(s['ci_level'] * 100)}% bootstrap CI "
                  f"[{s['ci_low']:.3f}, {s['ci_high']:.3f}]",
                  f"- Permutation p = {s['p_value_permutation']:.4g} ({s['n_permutations']} permutations)",
                  f"- SESOI = {s['sesoi_g']} (g); verdict: `{s['verdict']}`", ""]
    lines += ["## Reasons", ""] + [f"- {r}" for r in a["reasons"]]
    if report["leakage"]["findings"]:
        lines += ["", "## Leakage findings", ""] + [f"- {f}" for f in report["leakage"]["findings"]]
    if report["validation"]["errors"]:
        lines += ["", "## Validation errors", ""] + [f"- {e}" for e in report["validation"]["errors"]]
    return "\n".join(lines) + "\n"


def write_report(report: Dict[str, Any], out_dir: PathLike) -> Dict[str, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{report['analysis_id']}.json"
    md_path = out_dir / f"{report['analysis_id']}.md"
    write_json(json_path, report)
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}
