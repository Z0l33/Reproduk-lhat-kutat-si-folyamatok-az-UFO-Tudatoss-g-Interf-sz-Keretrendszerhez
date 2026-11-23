"""Re-identification RISK ESTIMATE (not proof) from quasi-identifier combinations.

Output rules (enforced by tests):
  * only aggregate counts and frequency bands; no keys, codes, record ids or values;
  * any count between 1 and k-1 is suppressed ("<k"); k = 5, and k = 10 for minors;
  * groups smaller than 5 are reported merged as "rows in groups smaller than 5";
  * rows with missing or reduced-precision dates never enter a level that needs a full date;
  * confirmed person-bound age hits, unclassified age phrases and minors are separate cohorts.
A group of size 1 inside this file does not prove that a person can be identified, and a large
group does not prove safety: the file is not the whole population and attacker knowledge is assumed.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Dict, Hashable, List, Optional, Sequence

K = 5
K_MINORS = 10


def suppress(count: int, k: int = K) -> Any:
    return count if count == 0 or count >= k else f"<{k}"


def risk_band(share: float) -> str:
    return ("low" if share < 0.05 else "moderate" if share < 0.25
            else "high" if share < 0.75 else "very_high")


def _share_band(share: float) -> str:
    """Coarse band used for minors so that exact shares cannot expose small groups."""
    return "0%" if share == 0 else "1-24%" if share < 0.25 else "25-74%" if share < 0.75 else "75%+"


def evaluate(rows: Sequence[Dict[str, Any]], key: Callable[[Dict[str, Any]], Optional[Hashable]],
             subset: Optional[Callable[[Dict[str, Any]], bool]] = None, k_report: int = K) -> Dict[str, Any]:
    """Group the rows that can form `key` (None = not eligible); report aggregates for `subset`."""
    keys = [key(r) for r in rows]
    sizes = Counter(x for x in keys if x is not None)
    picked = [i for i, r in enumerate(rows) if keys[i] is not None and (subset is None or subset(r))]
    excluded = sum(1 for i, r in enumerate(rows) if keys[i] is None and (subset is None or subset(r)))
    n = len(picked)
    out: Dict[str, Any] = {"rows_excluded_missing_component_or_reduced_date": suppress(excluded, k_report)}
    if n < k_report:
        out.update(eligible_rows=suppress(n, k_report), status="suppressed: too few rows")
        return out
    small = sum(1 for i in picked if sizes[keys[i]] < K)
    share = small / n
    out["eligible_rows"] = n
    out["rows_in_groups_smaller_than_5"] = suppress(small, k_report)
    if k_report > K:  # minors: banded share only
        out["share_in_groups_smaller_than_5_band"] = _share_band(share)
    else:
        out["share_in_groups_smaller_than_5"] = round(share, 3)
        out["risk_indicator"] = risk_band(share)
    return out


def _levels(prefix: str) -> List[str]:
    return [f"{prefix}1_plz", f"{prefix}2_plz_age", f"{prefix}3_plz_age_date",
            f"{prefix}4_plz_age_date_time", f"{prefix}5_place_age_date",
            f"{prefix}6_generalised_plz2_ageband_month"]


def _age_key(r: Dict[str, Any]) -> Optional[tuple]:
    return tuple(sorted(r["ages"])) if r["ages"] else None


def _band10(a: int) -> int:
    return a // 10


def keys_with_age() -> Dict[str, Callable[[Dict[str, Any]], Optional[Hashable]]]:
    def plz(r): return r["plz"]
    def l2(r): return (r["plz"], _age_key(r)) if r["plz"] and r["ages"] else None
    def l3(r): return (r["plz"], _age_key(r), r["date"]) if r["plz"] and r["ages"] and r["date"] else None
    def l4(r): return ((r["plz"], _age_key(r), r["date"], r["time"])
                       if r["plz"] and r["ages"] and r["date"] and r["time"] else None)
    def l5(r): return (r["place"], _age_key(r), r["date"]) if r["place"] and r["ages"] and r["date"] else None
    def l6(r): return ((r["plz"][:2], tuple(sorted({_band10(a) for a in r["ages"]})), r["date"][:7])
                       if r["plz"] and r["ages"] and r["date"] else None)
    return dict(zip(_levels("L"), [lambda r: r["plz"] or None, l2, l3, l4, l5, l6]))


def keys_without_age() -> Dict[str, Callable[[Dict[str, Any]], Optional[Hashable]]]:
    return {
        "N1_plz": lambda r: r["plz"] or None,
        "N2_plz_date": lambda r: (r["plz"], r["date"]) if r["plz"] and r["date"] else None,
        "N3_plz_date_time": lambda r: (r["plz"], r["date"], r["time"]) if r["plz"] and r["date"] and r["time"] else None,
        "N4_place_date_time": lambda r: (r["place"], r["date"], r["time"]) if r["place"] and r["date"] and r["time"] else None,
    }


def assess(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """rows: dicts with plz (digits-only PLZ or ''), place (hashed/normalised text or ''), date
    (YYYY-MM-DD for FULL-precision dates only, else ''), time, ages (list[int]), cohort
    ('strict' | 'unclassified' | 'none')."""
    strict = [r for r in rows if r["cohort"] == "strict"]
    unclass = [dict(r, ages=r["ages"]) for r in rows if r["cohort"] == "unclassified"]
    out: Dict[str, Any] = {
        "kind": "risk estimate, not proof of identifiability",
        "risk_indicator_bands": "share of eligible rows in groups smaller than 5: below 5% low, below 25% "
                                "moderate, below 75% high, otherwise very_high. Thresholds are heuristic "
                                "conventions, not legal or statistical standards.",
        "suppression": {"general_threshold": K, "minors_threshold": K_MINORS,
                        "groups_smaller_than_5": "merged into one band, never listed"},
        "assumptions": [
            "uniqueness is measured inside this file only; the real population is larger, so a unique "
            "row here may or may not be unique in reality",
            "an attacker is assumed to know some of: place, date, time, a person's age",
            "age comes from a heuristic text screen; it may belong to a person other than the reporter",
            "reduced-precision and missing dates are excluded from every level that needs a full date"],
        "cohorts": {"strict_person_bound_age": suppress(len(strict)),
                    "unclassified_age_phrase_not_verified": suppress(len(unclass)),
                    "minors_any_strict_age_under_18": suppress(
                        sum(1 for r in strict if any(a < 18 for a in r["ages"])), K_MINORS)},
    }
    out["without_age_all_rows"] = {name: evaluate(rows, fn) for name, fn in keys_without_age().items()}
    levels = keys_with_age()
    out["strict_age_cohort"] = {name: evaluate(strict, fn) for name, fn in levels.items()}
    out["unclassified_cohort_not_verified"] = {name: evaluate(unclass, fn) for name, fn in levels.items()}
    is_minor = lambda r: any(a < 18 for a in r["ages"])  # noqa: E731
    out["minors_strict_cohort_banded_and_suppressed"] = {
        name: evaluate(strict, fn, subset=is_minor, k_report=K_MINORS) for name, fn in levels.items()}
    out["handling_note"] = ("Rows with minor ages must not be redistributed or published at place level; "
                            "treat the minors cohort as restricted data.")
    return out
