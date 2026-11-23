"""Effect sizes, bootstrap confidence intervals, permutation tests and SESOI verdicts."""
from __future__ import annotations

import math
from typing import Any, Dict, Sequence

import numpy as np

VERDICTS = ("above_sesoi", "equivalent_to_zero_within_sesoi",
            "significant_but_not_above_sesoi", "inconclusive")


def _clean(x: Sequence[float], label: str) -> np.ndarray:
    arr = np.asarray(x, dtype=float)
    if arr.ndim != 1 or arr.size < 2 or not np.all(np.isfinite(arr)):
        raise ValueError(f"{label}: need a 1-D array of >=2 finite values")
    return arr


def hedges_g(x: Sequence[float], y: Sequence[float]) -> float:
    """Hedges' g (small-sample-corrected standardized mean difference, x minus y)."""
    a, b = _clean(x, "x"), _clean(y, "y")
    na, nb = a.size, b.size
    pooled_var = ((na - 1) * a.var(ddof=1) + (nb - 1) * b.var(ddof=1)) / (na + nb - 2)
    if pooled_var <= 0:
        raise ValueError("pooled variance is zero; effect size undefined")
    d = (a.mean() - b.mean()) / math.sqrt(pooled_var)
    return float(d * (1 - 3 / (4 * (na + nb) - 9)))


def bootstrap_ci_g(x: Sequence[float], y: Sequence[float], *, n_boot: int, alpha: float,
                   rng: np.random.Generator) -> tuple:
    """Percentile bootstrap (1-alpha) CI for Hedges' g."""
    a, b = _clean(x, "x"), _clean(y, "y")
    values = np.empty(n_boot)
    for i in range(n_boot):
        ra = a[rng.integers(0, a.size, a.size)]
        rb = b[rng.integers(0, b.size, b.size)]
        values[i] = hedges_g(ra, rb) if (ra.var() > 0 or rb.var() > 0) else 0.0
    return (float(np.quantile(values, alpha / 2)), float(np.quantile(values, 1 - alpha / 2)))


def permutation_pvalue(x: Sequence[float], y: Sequence[float], *, n_perm: int,
                       rng: np.random.Generator, chunk: int = 1000) -> float:
    """Two-sided permutation p-value for the difference in means, (count+1)/(n_perm+1)."""
    a, b = _clean(x, "x"), _clean(y, "y")
    pooled = np.concatenate([a, b])
    observed = abs(a.mean() - b.mean())
    count = 0
    done = 0
    while done < n_perm:
        size = min(chunk, n_perm - done)
        order = rng.random((size, pooled.size)).argsort(axis=1)
        left = pooled[order[:, : a.size]].mean(axis=1)
        right = pooled[order[:, a.size:]].mean(axis=1)
        count += int(np.sum(np.abs(left - right) >= observed - 1e-12))
        done += size
    return (count + 1) / (n_perm + 1)


def effect_verdict(g: float, ci_low: float, ci_high: float, p_value: float,
                   sesoi: float, alpha: float) -> str:
    """Classify an effect against the smallest effect size of interest (SESOI).

    above_sesoi: p < alpha AND the whole CI lies beyond +/-SESOI on one side.
    equivalent_to_zero_within_sesoi: the whole CI lies inside (-SESOI, +SESOI).
    significant_but_not_above_sesoi: p < alpha but the CI does not clear the SESOI.
    inconclusive: everything else.
    Only 'above_sesoi' can ever support a hypothesis.
    """
    if sesoi <= 0:
        raise ValueError("SESOI must be > 0")
    if ci_low > -sesoi and ci_high < sesoi:
        return "equivalent_to_zero_within_sesoi"
    if p_value < alpha and (ci_low > sesoi or ci_high < -sesoi):
        return "above_sesoi"
    if p_value < alpha:
        return "significant_but_not_above_sesoi"
    return "inconclusive"


def analyze_two_groups(x: Sequence[float], y: Sequence[float], *, sesoi: float, alpha: float = 0.05,
                       n_permutations: int = 10000, n_bootstrap: int = 5000,
                       seed: int = 0) -> Dict[str, Any]:
    if not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)")
    a, b = _clean(x, "group A"), _clean(y, "group B")
    boot_seed, perm_seed = np.random.SeedSequence(seed).spawn(2)
    g = hedges_g(a, b)
    lo, hi = bootstrap_ci_g(a, b, n_boot=n_bootstrap, alpha=alpha, rng=np.random.default_rng(boot_seed))
    p = permutation_pvalue(a, b, n_perm=n_permutations, rng=np.random.default_rng(perm_seed))
    return {
        "n_a": int(a.size), "n_b": int(b.size),
        "mean_a": float(a.mean()), "mean_b": float(b.mean()),
        "sd_a": float(a.std(ddof=1)), "sd_b": float(b.std(ddof=1)),
        "hedges_g": g, "ci_level": 1 - alpha, "ci_low": lo, "ci_high": hi,
        "p_value_permutation": p, "alpha": alpha, "sesoi_g": sesoi,
        "n_permutations": n_permutations, "n_bootstrap": n_bootstrap, "seed": seed,
        "verdict": effect_verdict(g, lo, hi, p, sesoi, alpha),
    }
