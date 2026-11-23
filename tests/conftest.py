"""Test isolation.

* Every test runs in its own empty working directory, so nothing can read or write repo-relative paths
  implicitly and parallel processes cannot collide on them.
* Module-level fixture constants are snapshotted; a test that mutates one fails immediately (instead of
  silently polluting later tests, possibly in another order or process).
* UFO_TEST_SHUFFLE_SEED=<int> runs the tests in a shuffled order (used by CI to expose order dependence).
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
