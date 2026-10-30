"""Pure same-policy final-fit parity, with an independent pre-change oracle.

No compiler, worker subprocess, process sampler or native qualification runs.
"""

import importlib.util
from pathlib import Path
import random

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "worker_final_fit_policy", ROOT / "pcc/frontends/python/worker_resource_plan.py",
)
plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan)


def reference_choose(pending, tasks, observations, active_reservations,
                     width, available, guarded_calibration=False):
    """Literal selection algorithm from f88c01df, before final-fit folding."""
    if len(active_reservations) >= width:
        return -1, 0, False
    for index in pending:
        if tasks[index].get("retry_calibration", False):
            if active_reservations:
                return -1, 0, False
            return index, max(available, plan.estimated_task_bytes(tasks[index], observations)), True
    if any(tasks[index].get("startup_prior_model", "") for index in pending):
        pending = plan._sort_modeled_tasks(pending, tasks, observations)
        if (pending and guarded_calibration and available > 0
                and tasks[pending[0]].get("startup_prior_model", "")
                and plan.estimated_task_bytes(tasks[pending[0]], observations) > available):
            if active_reservations:
                return -1, 0, False
            index = pending[0]
            return index, plan.estimated_task_bytes(tasks[index], observations), True
    calibration = -1
    for index in pending:
        if (tasks[index].get("calibrate_before_peers", False)
                and plan.estimated_task_bytes(tasks[index], observations) == 0
                and (calibration < 0 or index < calibration)):
            calibration = index
    if calibration >= 0:
        if active_reservations or available <= 0:
            return -1, 0, False
        return calibration, available, True
    remaining = available - sum(active_reservations)
    unknown = -1
    forecast_only = -1
    for index in pending:
        demand = plan.estimated_task_bytes(tasks[index], observations)
        if demand == 0:
            if unknown < 0:
                unknown = index
        elif demand <= remaining:
            return index, demand, False
        elif (guarded_calibration and demand > available and forecast_only < 0
              and plan.minimum_task_bytes(tasks[index]) <= available):
            forecast_only = index
    if not active_reservations and unknown >= 0 and available > 0:
        return unknown, available, True
    if not active_reservations and forecast_only >= 0 and available > 0:
        return forecast_only, plan.estimated_task_bytes(tasks[forecast_only], observations), True
    return -1, 0, False


def task(scale=1, execution_class="codegen"):
    return {
        "class": execution_class,
        "inputs": [1000 * scale, 1000 * scale, 3000 * scale, 3000 * scale, 27096662, 1],
        "estimate_bytes": 0,
        "startup_prior_model": "host-codegen-v1",
    }


def observation(owner, peak=1000):
    return (owner["class"], list(owner["inputs"]), peak,
            (owner["startup_prior_model"], plan.task_startup_prior_bytes(owner)))


def outcome(function, *args):
    try:
        return "value", function(*args)
    except Exception as error:
        return "error", type(error), str(error)


def compare(factory):
    results = []
    for choose in (reference_choose, plan.choose_task):
        args, events = factory()
        results.append((outcome(choose, *args), events))
    assert results[0] == results[1]
    return results[1][0]


def test_nonfitting_plain_scan_folds_history_once_instead_of_per_task(monkeypatch):
    tasks = [task(index + 1) for index in range(24)]
    rows = [observation(tasks[0], 1000 + index) for index in range(60)]
    saved = plan.peak_reservation
    calls = []

    def counted(peak):
        calls.append(peak)
        return saved(peak)

    monkeypatch.setattr(plan, "peak_reservation", counted)
    arguments = list(range(24)), tasks, rows, [2**38], 2, 2**40, False
    # Reserve all but one byte without changing any prior or floor.
    arguments = (*arguments[:3], [2**40 - 1], *arguments[4:])
    assert plan.choose_task(*arguments) == (-1, 0, False)
    folded = sum(value in range(1000, 1060) for value in calls)
    calls.clear()
    assert reference_choose(*arguments) == (-1, 0, False)
    original = sum(value in range(1000, 1060) for value in calls)
    assert (folded, original) == (60 * 2, 60 * 25)


def test_fresh_scan_observes_calibration_hook_and_reservation_arithmetic():
    def factory():
        tasks = [task(3), task(2), task(1)]
        rows = [observation(tasks[0])]
        events = []

        class Calibration:
            def __bool__(self):
                events.append("calibration")
                rows[0] = observation(tasks[0], 2**34)
                return False

        class Reservation:
            def __radd__(self, value):
                events.append("reservation")
                rows[0] = observation(tasks[0], 2**35)
                return value + 2**40 - 1

        tasks[-1]["calibrate_before_peers"] = Calibration()
        return ([0, 1, 2], tasks, rows, [Reservation()], 2, 2**40, False), events

    assert compare(factory) == ("value", (-1, 0, False))


@pytest.mark.parametrize("kind", ["custom-key", "custom-value", "custom-inputs", "custom-dict"])
def test_custom_task_operations_clear_prior_fit_snapshot(kind):
    def factory():
        tasks = [task(3), task(2), task(1)]
        rows = [observation(tasks[0])]
        events = []
        fitting = [False]

        def mutate(label):
            if fitting[0]:
                events.append(label)
                rows[0] = observation(tasks[0], 2**35)

        class Collision:
            def __hash__(self):
                return hash("class")

            def __eq__(self, other):
                mutate("key")
                return False

        class Estimate:
            def __int__(self):
                mutate("value")
                return 0

        class Inputs(list):
            def __iter__(self):
                mutate("inputs")
                return super().__iter__()

        class Task(dict):
            def get(self, key, default=None):
                mutate("dict-get:" + key)
                return super().get(key, default)

        class Calibration:
            def __bool__(self):
                fitting[0] = True
                events.append("begin-fit")
                return False

        if kind == "custom-key":
            tasks[1] = {Collision(): None, **tasks[1]}
        elif kind == "custom-value":
            tasks[1]["estimate_bytes"] = Estimate()
        elif kind == "custom-inputs":
            tasks[1]["inputs"] = Inputs(tasks[1]["inputs"])
        else:
            tasks[1] = Task(tasks[1])
        tasks[-1]["calibrate_before_peers"] = Calibration()
        return ([0, 1, 2], tasks, rows, [2**40 - 1], 2, 2**40, False), events

    assert compare(factory) == ("value", (-1, 0, False))


@pytest.mark.parametrize("kind", ["tasks", "available", "guarded", "index"])
def test_custom_sequence_indices_and_comparisons_keep_lazy_scan(kind):
    def factory():
        tasks = [task(3), task(2), task(1)]
        stable_owner = tasks[0]
        rows = [observation(tasks[0])]
        events = []
        phase = [False]

        def mutate(label):
            if phase[0]:
                rows[0] = observation(stable_owner, 2**35)
                events.append(label)

        class Tasks(list):
            def __getitem__(self, index):
                mutate("task-index")
                return super().__getitem__(index)

        class Remaining(int):
            def __ge__(self, other):
                mutate("remaining-comparison")
                return super().__ge__(other)

        class Available(int):
            def __sub__(self, other):
                return Remaining(int(self) - other)

        class Guarded:
            def __bool__(self):
                mutate("guarded")
                return False

        class Index(int):
            pass

        class Calibration:
            def __bool__(self):
                phase[0] = True
                events.append("begin-fit")
                return False

        tasks[-1]["calibrate_before_peers"] = Calibration()
        if kind == "tasks":
            tasks = Tasks(tasks)
        pending = [Index(0), 1, 2] if kind == "index" else [0, 1, 2]
        available = Available(2**40) if kind == "available" else 2**40
        guarded = Guarded() if kind == "guarded" else False
        return (pending, tasks, rows, [2**40 - 1], 2, available, guarded), events

    assert compare(factory) == ("value", (-1, 0, False))


def test_custom_observation_and_legacy_envelopes_preserve_callbacks():
    def factory():
        tasks = [task(3), task(2), task(1)]
        events = []

        class Inputs(list):
            def __iter__(self):
                events.append("legacy-inputs")
                return super().__iter__()

        rows = [("codegen", Inputs(tasks[0]["inputs"]), 2**30), observation(tasks[0])]
        return ([0, 1, 2], tasks, rows, [2**40 - 1], 2, 2**40, False), events

    compare(factory)


def test_replaced_custom_task_finalizes_before_active_reservation_truthiness():
    def factory():
        phase = [False]
        deleted = [False]
        events = []
        plain = {"class": "unknown", "inputs": [], "estimate_bytes": 0}

        class Task(dict):
            def get(self, key, default=None):
                if phase[0] and key == "startup_prior_model":
                    events.append("replace")
                    tasks[0] = dict(plain)
                return super().get(key, default)

            def __del__(self):
                events.append("delete")
                deleted[0] = True

        class Active(list):
            def __iter__(self):
                events.append("sum")
                phase[0] = True
                return super().__iter__()

            def __bool__(self):
                events.append(("active-bool", deleted[0]))
                return not deleted[0]

        tasks = [Task(plain)]
        return ([0], tasks, [], Active([1]), 2, 100, False), events

    assert compare(factory) == ("value", (0, 100, True))


def test_fallback_can_replace_future_index_and_mutate_history_on_lookup():
    def factory():
        phase = [False]
        events = []
        pending = [0, 1, 2]
        tasks = []
        rows = [("x", [], 1, ("host-codegen-v1", 2**30))]

        def plain():
            return {"class": "x", "inputs": [1] * 6, "estimate_bytes": 0,
                    "startup_prior_model": ""}

        class Index:
            def __index__(self):
                events.append("index")
                rows[0] = ("x", [], 2**35, ("host-codegen-v1", 2**30))
                return 2

        class Task(dict):
            def get(self, key, default=None):
                if phase[0] and key == "startup_prior_model":
                    pending[2] = Index()
                    events.append("replace-index")
                return super().get(key, default)

        class Active(list):
            def __iter__(self):
                phase[0] = True
                for owner in tasks:
                    owner["startup_prior_model"] = "host-codegen-v1"
                return super().__iter__()

        tasks.extend([Task(plain()), plain(), plain()])
        tasks[0]["estimate_bytes"] = tasks[1]["estimate_bytes"] = 2**38
        return (pending, tasks, rows, Active([2**40 - 2**30]), 2, 2**40, False), events

    assert compare(factory) == ("value", (-1, 0, False))


def test_replaced_custom_inputs_finalize_before_estimator_reads_observations():
    def factory():
        phase = [False]
        events = []
        rows = [("x", [], 1, ("host-codegen-v1", 2**30))]

        class Inputs(list):
            def __iter__(self):
                if phase[0]:
                    events.append("replace-inputs")
                    owner["inputs"] = [1] * 6
                return super().__iter__()

            def __del__(self):
                events.append("delete-inputs")
                rows[0] = ("x", [], 2**35, ("host-codegen-v1", 2**30))

        class Active(list):
            def __iter__(self):
                phase[0] = True
                return super().__iter__()

        owner = {"class": "x", "inputs": Inputs([1] * 6), "estimate_bytes": 0,
                 "startup_prior_model": "host-codegen-v1"}
        return ([0], [owner], rows, Active([2**40 - 2**30]), 2, 2**40, False), events

    assert compare(factory) == ("value", (-1, 0, False))


@pytest.mark.parametrize("bad", [None, ("codegen",), ("foreign",),
                                ("codegen", [], 1, None),
                                ("codegen", [], 1, ("host-codegen-v1", 0)),
                                ("foreign", [], -1, None)])
def test_malformed_history_keeps_exact_error_and_lazy_width_exit(bad):
    for active in ([2**40 - 1], [1, 2]):
        def factory():
            tasks = [task(3), task(2), task(1)]
            return ([0, 1, 2], tasks, [bad], active, 2, 2**40, False), []

        compare(factory)


def test_independent_differential_all_models_floors_retries_and_large_integers():
    rng = random.Random(714223)
    models = ["host-export-v1", "host-summary-v1", "host-codegen-v1",
              "host-indexed-frontend-v1", "host-indexed-backend-v1", "host-indexed-backend-v2", ""]
    for _ in range(120):
        owners = [task(rng.randrange(1, 10000), str(rng.randrange(3))) for _ in range(7)]
        for owner in owners:
            model = rng.choice(models)
            owner["startup_prior_model"] = model
            owner["estimate_bytes"] = rng.choice([0, 2**37 + 19])
            owner["incomplete_peak_bytes"] = rng.choice([0, 2**36 + 7])
            owner["retry_calibration"] = rng.randrange(30) == 0
            if model.startswith("host-indexed-backend-"):
                owner.update(inputs=[1234, 2, 3, 800, 80], input_ready=True, prior_input_bytes=1234)
        rows = []
        for owner in owners:
            if owner["startup_prior_model"]:
                rows.append(observation(owner, rng.choice([1000, 2**115 + 7])))
            else:
                rows.append((owner["class"], list(owner["inputs"]), 1000))
        active = rng.choice([[], [2**35], [1, 2]])
        available = rng.choice([-1, 0, 1, 2**30, 2**40, 2**120])
        guarded = bool(rng.randrange(2))
        arguments = list(range(7)), owners, rows, active, 2, available, guarded
        assert outcome(plan.choose_task, *arguments) == outcome(reference_choose, *arguments)
