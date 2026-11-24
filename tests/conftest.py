"""Test isolation.

* Every test runs in its own empty working directory, so nothing can read or write repo-relative paths
  implicitly and parallel processes cannot collide on them.
* Module-level fixture constants are snapshotted; a test that mutates one fails immediately (instead of
  silently polluting later tests, possibly in another order or process).
* UFO_TEST_SHUFFLE_SEED=<int> runs the tests in a shuffled order (used by CI to expose order dependence).
* UFO_REQUIRE_BROWSER=1 (set by CI) turns a skip caused by a missing Playwright/Chromium into a failure, so the
  browser-rendered checks cannot silently drop out of CI. Locally, without the variable, they skip as before.
"""
import copy
import os
import random

import pytest

from tests import helpers

_SNAPSHOTS = {name: copy.deepcopy(getattr(helpers, name)) for name in ("ANALYSIS", "VALIDATION")}


@pytest.fixture(autouse=True)
def _isolated_cwd_and_constants(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield
    for name, snapshot in _SNAPSHOTS.items():
        assert getattr(helpers, name) == snapshot, (
            f"tests.helpers.{name} was mutated by this test; copy it (copy.deepcopy) before changing it")


def pytest_collection_modifyitems(config, items):
    seed = os.environ.get("UFO_TEST_SHUFFLE_SEED")
    if seed is not None:
        random.Random(int(seed)).shuffle(items)


_BROWSER_SKIP_MARKERS = ("playwright", "browser unavailable", "chromium")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if os.environ.get("UFO_REQUIRE_BROWSER") == "1" and report.skipped:
        reason = str(report.longrepr).lower()
        if any(marker in reason for marker in _BROWSER_SKIP_MARKERS):
            report.outcome = "failed"
            report.longrepr = ("UFO_REQUIRE_BROWSER=1 but a browser-rendered test was skipped "
                               f"(Playwright/Chromium missing?): {report.longrepr}")
