"""The dedicated callback stop condition only wakes for the owner predicate."""
from pathlib import Path

import pytest

from tests.python.runtime_body_model import load_functions


KERNEL = (Path(__file__).resolve().parents[2]
          / "pcc/runtime/py/freestanding_thread_kernel_pthread.py")


@pytest.mark.parametrize("live,parked,exited,expected", (
    (5, 1, 1, []),
    (5, 3, 1, [17]),
    (4, 2, 1, [17]),
    (2, 0, 0, []),
    (1, 0, 0, [17]),
))
def test_callback_owner_notification_uses_readiness_predicate(live, parked, exited, expected):
    data = {
        "pcc_live_thread_count_py": live,
        "pcc_parked_thread_count_py": parked,
        "pcc_exited_thread_count_py": exited,
    }
    notifications = []
    ns = {
        "_world_i64": lambda val: val,
        "global_addr": data.__getitem__,
        "pcc_cond_signal": lambda condition: notifications.append(condition) or 0,
        "pcc_platform_abort": lambda: pytest.fail("unexpected signal failure"),
    }
    load_functions(KERNEL, {"_signal_callback_owner_when_ready"}, ns)
    ns["_signal_callback_owner_when_ready"](17)
    assert notifications == expected
