"""Evidence classification: the single place that decides what counts as positive evidence.

Design rule: `counts_as_positive_evidence` is True ONLY for the two statuses in
POSITIVE_STATUSES. Invalid data, leakage, unverified data, synthetic data, exploratory
analyses, null results, effects not clearly above the SESOI, wrong-direction effects and
failed or invalid replications can never reach them.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

POSITIVE_STATUSES = ("confirmatory_positive_replicated", "confirmatory_positive_unreplicated")
NON_POSITIVE_CONFIRMATORY = ("confirmatory_null_equivalence", "confirmatory_inconclusive",
                             "confirmatory_below_sesoi_or_uncertain", "confirmatory_wrong_direction")
POSITIVE_ANALYSIS_STATUSES = POSITIVE_STATUSES + ("awaiting_independent_replication",)


@dataclass(frozen=True)
class Assessment:
    status: str
    counts_as_positive_evidence: bool
    mode: str  # "confirmatory" | "exploratory"
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def assess(*, data_valid: bool, leakage_detected: bool, dataset_verified: bool, synthetic: bool,
           prereg_problems: Optional[List[str]], analysis: Optional[Dict[str, Any]],
           expected_direction: str = "any", require_replication: bool = True,
           replication_outcome: Optional[str] = None) -> Assessment:
    """Classify one analysis. `prereg_problems=None` means no preregistration was supplied."""
    reasons: List[str] = []
    mode = "confirmatory" if prereg_problems == [] else "exploratory"
    if prereg_problems is None:
        reasons.append("no preregistration supplied -> exploratory")
    else:
        reasons += [f"preregistration problem: {p}" for p in prereg_problems]

    def out(status: str) -> Assessment:
        positive = status in POSITIVE_STATUSES
        # Defensive invariant: no blocking condition may ever coexist with a positive flag.
        if positive and (not data_valid or leakage_detected or synthetic or not dataset_verified
                         or mode != "confirmatory" or analysis is None
                         or analysis.get("verdict") != "above_sesoi"
                         or replication_outcome in ("failed", "invalid")):
            raise AssertionError("invariant violated: positive evidence with a blocking condition")
        return Assessment(status, positive, mode, reasons)

    if not data_valid:
        reasons.append("data validation failed")
        return out("invalid_data")
    if leakage_detected:
        reasons.append("data leakage detected; result is not interpretable")
        return out("invalid_leakage")
    if synthetic:
        reasons.append("synthetic test data are never evidence")
        return out("synthetic_not_evidence")
    if not dataset_verified:
        reasons.append("dataset integrity not verified against source checksum and SHA-256 pin")
        return out("unverified_data")
    if analysis is None:
        reasons.append("no analysis result")
        return out("no_analysis")

    verdict = analysis["verdict"]
    if mode == "exploratory":
        reasons.append(f"exploratory effect verdict: {verdict}; hypothesis-generating only")
        return out("exploratory_hypothesis_generating")

    if verdict == "equivalent_to_zero_within_sesoi":
        reasons.append("effect is equivalent to zero within the SESOI (null/negligible result)")
        return out("confirmatory_null_equivalence")
    if verdict == "inconclusive":
        reasons.append("CI does not exclude zero effect nor establish an effect above the SESOI")
        return out("confirmatory_inconclusive")
    if verdict == "significant_but_not_above_sesoi":
        reasons.append("detectable but not shown to exceed the SESOI; not positive evidence")
        return out("confirmatory_below_sesoi_or_uncertain")

    g = analysis["hedges_g"]
    if (expected_direction == "a>b" and g <= 0) or (expected_direction == "a<b" and g >= 0):
        reasons.append("effect is in the opposite direction to the preregistered prediction")
        return out("confirmatory_wrong_direction")
    if replication_outcome == "failed":
        reasons.append("independent replication failed; the finding does not count as positive")
        return out("replication_failed")
    if replication_outcome == "invalid":
        reasons.append("submitted replication was not valid/independent; it is ignored")
        return out("replication_invalid_not_counted")
    if replication_outcome == "replicated":
        reasons.append("independent preregistered replication succeeded")
        return out("confirmatory_positive_replicated")
    if require_replication:
        reasons.append("preregistration requires independent replication; none yet")
        return out("awaiting_independent_replication")
    reasons.append("confirmatory result above SESOI, not independently replicated")
    return out("confirmatory_positive_unreplicated")


def evaluate_replication(original: Dict[str, Any], replication: Dict[str, Any]) -> Dict[str, Any]:
    """Compare an original analysis report with an independent replication report.

    Returns {"outcome": "replicated"|"failed"|"invalid", "reasons": [...]}.
    'invalid' = not a genuine independent replication (never counts for or against anything).
    """
    reasons: List[str] = []
    for label, report in (("original", original), ("replication", replication)):
        if not (report.get("data_access") or {}).get("mode"):
            reasons.append(f"{label} report has no data_access section: not produced by the "
                           "access-controlled pipeline")
    od, rd = original.get("dataset", {}), replication.get("dataset", {})
    if od.get("synthetic") or rd.get("synthetic"):
        reasons.append("synthetic data cannot establish or test replication")
    if od.get("dataset_id") == rd.get("dataset_id"):
        reasons.append("same dataset_id: not independent")
    if od.get("record_id") and od.get("record_id") == rd.get("record_id"):
        reasons.append("same Zenodo record: not independent")
    shared = set((od.get("file_sha256") or {}).values()) & set((rd.get("file_sha256") or {}).values())
    if shared:
        reasons.append("replication shares identical file content with the original")
    o_pre, r_pre = original.get("preregistration") or {}, replication.get("preregistration") or {}
    if not o_pre.get("sha256") or r_pre.get("replicates_prereg_sha256") != o_pre.get("sha256"):
        reasons.append("replication preregistration does not reference the original preregistration hash")
    o_assess, r_assess = original.get("assessment", {}), replication.get("assessment", {})
    if o_assess.get("status") not in ("awaiting_independent_replication",
                                      "confirmatory_positive_unreplicated"):
        reasons.append("original is not a confirmatory positive finding awaiting replication")
    if r_assess.get("mode") != "confirmatory":
        reasons.append("replication is not a confirmatory (preregistered) analysis")
    if reasons:
        return {"outcome": "invalid", "reasons": reasons}

    r_status = r_assess.get("status")
    o_g = (original.get("analysis") or {}).get("hedges_g")
    r_g = (replication.get("analysis") or {}).get("hedges_g")
    if r_status in POSITIVE_ANALYSIS_STATUSES and o_g is not None and r_g is not None and o_g * r_g > 0:
        return {"outcome": "replicated", "reasons": ["independent confirmatory replication above SESOI, same direction"]}
    if r_status in POSITIVE_ANALYSIS_STATUSES:
        return {"outcome": "failed", "reasons": ["replication effect has the opposite sign to the original"]}
    if r_status in NON_POSITIVE_CONFIRMATORY:
        return {"outcome": "failed", "reasons": [f"valid independent replication did not reproduce the effect ({r_status})"]}
    return {"outcome": "invalid", "reasons": [f"replication status {r_status!r} cannot be used to judge replication"]}
