"""Pure host forecast equivalence; no workers, sampling or compiler execution."""

from contextlib import contextmanager
import importlib.util
from pathlib import Path
import random

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "worker_estimate_ratio_fold", ROOT / "pcc/frontends/python/worker_resource_plan.py",
)
plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan)


def task(scale=1, execution_class="backend", model="host-indexed-backend-v2"):
    return {
        "class": execution_class,
        "inputs": [1000 * scale, 2, 3, 80 * scale, 20 * scale],
        "estimate_bytes": 0,
        "startup_prior_model": model,
        "input_ready": True,
        "prior_input_bytes": 1000 * scale,
    }


def observation(owner, peak, prior=None):
    if prior is None:
        prior = plan.task_startup_prior_bytes(owner)
    return owner["class"], list(owner["inputs"]), peak, (owner["startup_prior_model"], prior)


def outcome(function, *args, **kwargs):
    try:
        return "value", function(*args, **kwargs)
    except Exception as error:
        return "error", type(error), str(error)


def assert_estimate_equal(owner, observations):
    assert outcome(plan._choose_task_estimate, owner, observations, {}) == outcome(
        plan.estimated_task_bytes, owner, observations,
    )


@contextmanager
def uncached_selection():
    saved = plan._choose_task_estimate
    saved_fit = plan._foldable_fit_estimate
    # Disable both independent fold boundaries for the original raw oracle.
    plan._foldable_fit_estimate = lambda owner, observations, cache: None
    plan._choose_task_estimate = lambda owner, observations, cache: plan.estimated_task_bytes(
        owner, observations,
    )
    try:
        yield
    finally:
        plan._choose_task_estimate = saved
        plan._foldable_fit_estimate = saved_fit


def assert_selection_equal(tasks, observations, active=(), available=2**40, width=3,
                           guarded=True, pending=None):
    if pending is None:
        pending = list(range(len(tasks)))
    args = pending, tasks, observations, list(active), width, available
    actual = outcome(plan.choose_task, *args, guarded_calibration=guarded)
    with uncached_selection():
        expected = outcome(plan.choose_task, *args, guarded_calibration=guarded)
    assert actual == expected
    return actual


def test_all_modeled_priors_and_exact_integer_ratios():
    owners = [task(), task(model="host-indexed-backend-v1")]
    for model in ("host-export-v1", "host-summary-v1", "host-codegen-v1",
                  "host-indexed-frontend-v1"):
        owner = task(execution_class=model, model=model)
        owner["inputs"] = [100, 100, 700, 700, 1200, 1]
        owners.append(owner)
    for owner in owners:
        rows = [observation(owner, 2**35 + 7, 2**33 + 3),
                observation(owner, 2**120 + 19, 2**117 + 11)]
        assert_estimate_equal(owner, rows)
        assert_estimate_equal(owner, list(reversed(rows)))
        assert_selection_equal([owner], rows)


def test_maximum_ratio_is_selected_before_ceiling_without_float_conversion():
    owner = task(3)
    rows = [observation(owner, 2**101 + 1, 2**99 + 7),
            observation(owner, 2**101 + 9, 2**99 + 11)]
    ratio = plan._modeled_observation_ratio(rows, owner["class"], owner["startup_prior_model"])
    expected = max(rows, key=lambda row: plan.peak_reservation(row[2]) * rows[1][3][1]
                   if row is rows[0] else plan.peak_reservation(row[2]) * rows[0][3][1])
    assert ratio == (plan.peak_reservation(expected[2]), expected[3][1])
    assert_estimate_equal(owner, rows)


@pytest.mark.parametrize("explicit,incomplete", [(2**45, 0), (0, 2**44), (2**45, 2**46)])
def test_raw_explicit_and_incomplete_floors_are_unchanged(explicit, incomplete):
    owner = task()
    owner.update(estimate_bytes=explicit, incomplete_peak_bytes=incomplete)
    rows = [observation(owner, 200 * 1024**2)]
    assert_estimate_equal(owner, rows)
    assert_selection_equal([owner], rows)
    owner["retry_calibration"] = True
    assert_selection_equal([owner], rows, active=[1])
    assert_selection_equal([owner], rows, available=2**48)


def test_lazy_inputs_and_replacements_are_recomputed_each_invocation():
    owner = task()
    rows = [observation(owner, 900 * 1024**2)]
    owner["input_ready"] = False
    assert_estimate_equal(owner, rows)
    assert_selection_equal([owner], rows)
    owner["input_ready"] = True
    assert_selection_equal([owner], rows)
    owner["inputs"] = [2**30, 3, 4, 2**20, 2**18]
    owner["prior_input_bytes"] = 2**30
    assert_selection_equal([owner], rows)


def test_append_replacement_nested_mutation_and_class_model_changes_do_not_stale():
    owner = task()
    rows = [observation(owner, 100 * 1024**2)]
    first = assert_selection_equal([owner], rows)
    rows.append(observation(owner, 900 * 1024**2))
    second = assert_selection_equal([owner], rows)
    assert first != second
    rows[-1] = observation(owner, 1300 * 1024**2)
    assert_selection_equal([owner], rows)
    rows[-1][1][0] = 999999  # Modeled envelopes are intentionally not read.
    assert_selection_equal([owner], rows)
    owner["class"] = "other"
    assert_selection_equal([owner], rows)
    owner["class"] = "backend"
    owner["startup_prior_model"] = "host-indexed-backend-v1"
    assert_selection_equal([owner], rows)


@pytest.mark.parametrize("binding", [None, [], (), ("wrong", 2),
                                     ("host-indexed-backend-v2", 0),
                                     ("host-indexed-backend-v2", -3),
                                     ("host-indexed-backend-v2", True),
                                     ["host-indexed-backend-v2", 99]])
def test_relevant_malformed_bindings_fall_back_without_early_errors(binding):
    owner = task()
    rows = [(owner["class"], owner["inputs"], 5, binding)]
    assert plan._modeled_observation_ratio(rows, owner["class"], owner["startup_prior_model"]) is None
    assert_estimate_equal(owner, rows)
    assert_selection_equal([owner], rows)


@pytest.mark.parametrize("row", [("foreign", [], -1, None), ("foreign", [], 0),
                                 ("foreign", [], object(), ["invalid"]),
                                 ("foreign",), ("foreign", [], 1, None, None)])
def test_foreign_malformed_payloads_are_ignored_but_bad_row_lengths_still_fail(row):
    owner = task()
    assert_estimate_equal(owner, [row])
    assert_selection_equal([owner], [row])


@pytest.mark.parametrize("peak", [0, -1, None, "bad", 3.5, True])
def test_unsupported_peaks_preserve_original_acceptance_or_rejection(peak):
    owner = task()
    assert_estimate_equal(owner, [observation(owner, peak)])


def test_task_validation_and_first_bad_observation_keep_original_error_order():
    owner = task()
    malformed = (owner["class"], [], 1, None)
    bad_shape = ("foreign",)
    legacy = (owner["class"], ["bad"] * 5, 1)
    for rows in ([malformed, bad_shape], [bad_shape, malformed],
                 [legacy, malformed], [malformed, legacy]):
        assert_estimate_equal(owner, rows)
        assert_selection_equal([owner], rows)
    for mutation in ({"inputs": [-1, 2, 3, 4, 5]}, {"estimate_bytes": "bad"},
                     {"incomplete_peak_bytes": "bad"}):
        broken = dict(owner, **mutation)
        assert_estimate_equal(broken, [bad_shape])
        assert_selection_equal([broken], [bad_shape])
    # The width and draining-retry exits must not inspect history at all.
    assert_selection_equal([owner], [bad_shape], active=[1, 2, 3])
    owner["retry_calibration"] = True
    assert_selection_equal([owner], [bad_shape], active=[1])


def test_legacy_covering_envelopes_and_unmodeled_tasks_keep_original_path():
    owner = task()
    rows = [(owner["class"], [x + 1 for x in owner["inputs"]], 2**33),
            observation(owner, 2**32)]
    assert plan._modeled_observation_ratio(rows, owner["class"], owner["startup_prior_model"]) is None
    assert_estimate_equal(owner, rows)
    assert_selection_equal([owner], rows)
    owner["startup_prior_model"] = ""
    assert_estimate_equal(owner, rows)
    assert_selection_equal([owner], rows)
    rows[0][1][0] = -1
    assert_selection_equal([owner], rows)


def test_nonbuiltin_histories_and_bindings_are_not_eagerly_evaluated():
    class CustomList(list):
        pass

    class ExplosiveClass:
        def __eq__(self, other):
            raise RuntimeError("class comparison owns this failure")

    owner = task()
    for rows in (CustomList([observation(owner, 123)]),
                 [list(observation(owner, 123))],
                 [(ExplosiveClass(), [], 1, None)]):
        assert_estimate_equal(owner, rows)
        assert_selection_equal([owner], rows)


def test_selection_order_guarded_oversize_and_ties_are_unchanged():
    tasks = [task(10), task(2), task(10), task(100)]
    rows = [observation(tasks[0], 700 * 1024**2)]
    for guarded in (False, True):
        for available in (1, 200 * 1024**2, 900 * 1024**2, 2**40):
            for active in ([], [100], [2**35], [1, 2, 3]):
                assert_selection_equal(tasks, rows, active, available, guarded=guarded,
                                       pending=[2, 1, 0, 3])


def test_custom_task_conversion_cannot_leave_a_stale_ratio_for_later_tasks():
    def run(uncached):
        tasks = [task(1), task(2), task(3)]
        rows = [observation(tasks[0], 100 * 1024**2)]

        class ChangingEstimate:
            def __int__(self):
                rows[0] = observation(tasks[0], 900 * 1024**2)
                return 0

        tasks[1]["estimate_bytes"] = ChangingEstimate()
        if uncached:
            with uncached_selection():
                return plan.choose_task([0, 1, 2], tasks, rows, [], 3, 2**40, True)
        return plan.choose_task([0, 1, 2], tasks, rows, [], 3, 2**40, True)

    assert run(False) == run(True)


def test_custom_input_iterator_keeps_original_mutation_and_error_behavior():
    def run(uncached):
        owner = task()
        rows = [observation(owner, 100 * 1024**2)]

        class ChangingInputs(list):
            def __iter__(self):
                rows[0] = ("backend", [], 900 * 1024**2,
                           ("host-indexed-backend-v2", 218109008))
                return super().__iter__()

        owner["inputs"] = ChangingInputs(owner["inputs"])
        if uncached:
            return outcome(plan.estimated_task_bytes, owner, rows)
        return outcome(plan._choose_task_estimate, owner, rows, {})

    assert run(False) == run(True)


def test_calibration_hook_mutation_after_sort_is_seen_by_selection():
    def run(uncached):
        owner = task()
        rows = [observation(owner, 100 * 1024**2)]

        class ChangingCalibration:
            def __bool__(self):
                rows[0] = observation(owner, 900 * 1024**2)
                return False

        owner["calibrate_before_peers"] = ChangingCalibration()
        if uncached:
            with uncached_selection():
                return plan.choose_task([0], [owner], rows, [], 3, 2**40, True)
        return plan.choose_task([0], [owner], rows, [], 3, 2**40, True)

    assert run(False) == run(True)


def test_custom_task_sequence_keeps_uncached_index_side_effects():
    def run(uncached):
        owners = [task(1), task(2)]
        rows = [observation(owners[0], 100 * 1024**2)]

        class ChangingTasks(list):
            def __getitem__(self, index):
                rows[0] = observation(owners[0], (100 + 100 * index) * 1024**2)
                return super().__getitem__(index)

        tasks = ChangingTasks(owners)
        if uncached:
            with uncached_selection():
                return plan.choose_task([0, 1], tasks, rows, [], 3, 2**40, True)
        return plan.choose_task([0, 1], tasks, rows, [], 3, 2**40, True)

    assert run(False) == run(True)


def test_deterministic_differential_forecasts_and_selection():
    rng = random.Random(271828)
    for _ in range(100):
        tasks = [task(rng.randrange(1, 10000), str(rng.randrange(3))) for _ in range(8)]
        rows = [observation(rng.choice(tasks), rng.randrange(1, 2**36), rng.randrange(1, 2**35))
                for _ in range(20)]
        for owner in tasks:
            assert_estimate_equal(owner, rows)
        assert_selection_equal(tasks, rows, [rng.randrange(2**28)], rng.randrange(1, 2**38))


def test_sort_snapshot_folds_peaks_once_then_selection_reads_again(monkeypatch):
    tasks = [task(index + 1) for index in range(24)]
    rows = [observation(tasks[0], 1000 + index) for index in range(60)]
    saved = plan.peak_reservation
    calls = []

    def counted(peak):
        calls.append(peak)
        return saved(peak)

    monkeypatch.setattr(plan, "peak_reservation", counted)
    assert_selection_equal(tasks, rows)
    calls.clear()
    plan.choose_task(list(range(len(tasks))), tasks, rows, [], 3, 2**40, True)
    assert sum(value in range(1000, 1060) for value in calls) == 60 * 3
    calls.clear()
    with uncached_selection():
        plan.choose_task(list(range(len(tasks))), tasks, rows, [], 3, 2**40, True)
    assert sum(value in range(1000, 1060) for value in calls) == 60 * 26
