import math
import unittest

import numpy as np

from ufo_pipeline.stats import (analyze_two_groups, bootstrap_ci_g, effect_verdict, hedges_g,
                                permutation_pvalue)


class EffectSizeTests(unittest.TestCase):
    def test_hedges_g_hand_computed(self):
        x, y = [1.0, 2.0, 3.0], [2.0, 3.0, 4.0]
        pooled_sd = 1.0
        d = -1.0 / pooled_sd
        j = 1 - 3 / (4 * 6 - 9)
        self.assertAlmostEqual(hedges_g(x, y), d * j, places=12)

    def test_sign_and_antisymmetry(self):
        rng = np.random.default_rng(0)
        a, b = rng.normal(1, 1, 50), rng.normal(0, 1, 50)
        self.assertGreater(hedges_g(a, b), 0)
        self.assertAlmostEqual(hedges_g(a, b), -hedges_g(b, a), places=12)

    def test_zero_variance_rejected(self):
        with self.assertRaises(ValueError):
            hedges_g([1, 1, 1], [1, 1, 1])

    def test_bad_input_rejected(self):
        with self.assertRaises(ValueError):
            hedges_g([1.0], [1.0, 2.0])
        with self.assertRaises(ValueError):
            hedges_g([1.0, float("nan")], [1.0, 2.0])


class InferenceTests(unittest.TestCase):
    def test_permutation_p_null_is_large_and_effect_is_small(self):
        rng = np.random.default_rng(1)
        a, b = rng.normal(0, 1, 80), rng.normal(0, 1, 80)
        p_null = permutation_pvalue(a, b, n_perm=2000, rng=np.random.default_rng(2))
        p_eff = permutation_pvalue(a + 1.5, b, n_perm=2000, rng=np.random.default_rng(2))
        self.assertGreater(p_null, 0.05)
        self.assertAlmostEqual(p_eff, 1 / 2001, places=12)  # smallest attainable p

    def test_p_never_zero(self):
        a, b = np.arange(10.0) + 100, np.arange(10.0)
        self.assertGreater(permutation_pvalue(a, b, n_perm=500, rng=np.random.default_rng(0)), 0)

    def test_ci_contains_point_estimate(self):
        rng = np.random.default_rng(3)
        a, b = rng.normal(0.5, 1, 100), rng.normal(0, 1, 100)
        lo, hi = bootstrap_ci_g(a, b, n_boot=1000, alpha=0.05, rng=np.random.default_rng(4))
        self.assertLess(lo, hedges_g(a, b))
        self.assertGreater(hi, hedges_g(a, b))

    def test_reproducible_with_seed_and_differs_across_seeds(self):
        rng = np.random.default_rng(5)
        a, b = rng.normal(0.4, 1, 60), rng.normal(0, 1, 60)
        kw = dict(sesoi=0.2, n_permutations=500, n_bootstrap=300)
        r1, r2 = analyze_two_groups(a, b, seed=7, **kw), analyze_two_groups(a, b, seed=7, **kw)
        r3 = analyze_two_groups(a, b, seed=8, **kw)
        self.assertEqual(r1, r2)
        self.assertNotEqual((r1["ci_low"], r1["p_value_permutation"]), (r3["ci_low"], r3["p_value_permutation"]))

    def test_ci_coverage_is_reasonable(self):
        # Coverage of the true standardized effect (0.5) should be near 95%; allow a wide band.
        rng = np.random.default_rng(11)
        hits, reps = 0, 150
        for i in range(reps):
            a, b = rng.normal(0.5, 1, 60), rng.normal(0, 1, 60)
            lo, hi = bootstrap_ci_g(a, b, n_boot=300, alpha=0.05, rng=np.random.default_rng(i))
            hits += lo <= 0.5 <= hi
        self.assertGreater(hits / reps, 0.88)

    def test_false_positive_rate_near_alpha(self):
        rng = np.random.default_rng(13)
        reps, rejections = 200, 0
        for i in range(reps):
            a, b = rng.normal(0, 1, 30), rng.normal(0, 1, 30)
            rejections += permutation_pvalue(a, b, n_perm=199, rng=np.random.default_rng(i)) < 0.05
        self.assertLess(rejections / reps, 0.11)


class VerdictTests(unittest.TestCase):
    S, A = 0.2, 0.05

    def v(self, g, lo, hi, p):
        return effect_verdict(g, lo, hi, p, self.S, self.A)

    def test_table(self):
        self.assertEqual(self.v(0.8, 0.5, 1.1, 0.001), "above_sesoi")
        self.assertEqual(self.v(-0.8, -1.1, -0.5, 0.001), "above_sesoi")
        self.assertEqual(self.v(0.02, -0.1, 0.14, 0.7), "equivalent_to_zero_within_sesoi")
        self.assertEqual(self.v(0.3, 0.05, 0.55, 0.01), "significant_but_not_above_sesoi")  # CI straddles SESOI
        self.assertEqual(self.v(0.15, -0.3, 0.6, 0.2), "inconclusive")
        # Significant but the effect is tiny and fully inside the SESOI band: equivalent, not positive.
        self.assertEqual(self.v(0.1, 0.02, 0.18, 0.01), "equivalent_to_zero_within_sesoi")

    def test_sesoi_must_be_positive(self):
        with self.assertRaises(ValueError):
            effect_verdict(0.5, 0.1, 0.9, 0.01, 0.0, 0.05)


class ScenarioTests(unittest.TestCase):
    def kw(self, seed=0):
        return dict(sesoi=0.2, n_permutations=2000, n_bootstrap=1000, seed=seed)

    def test_true_effect_is_above_sesoi(self):
        rng = np.random.default_rng(21)
        r = analyze_two_groups(rng.normal(0.8, 1, 120), rng.normal(0, 1, 120), **self.kw())
        self.assertEqual(r["verdict"], "above_sesoi")

    def test_null_is_never_above_sesoi(self):
        rng = np.random.default_rng(22)
        r = analyze_two_groups(rng.normal(0, 1, 120), rng.normal(0, 1, 120), **self.kw())
        self.assertNotEqual(r["verdict"], "above_sesoi")

    def test_tiny_effect_large_n_is_equivalent(self):
        rng = np.random.default_rng(23)
        r = analyze_two_groups(rng.normal(0.05, 1, 4000), rng.normal(0, 1, 4000), **self.kw())
        self.assertEqual(r["verdict"], "equivalent_to_zero_within_sesoi")
        self.assertTrue(math.isfinite(r["hedges_g"]))


if __name__ == "__main__":
    unittest.main()
