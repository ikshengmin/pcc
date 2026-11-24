"""Native public snapshots exclude dead cycles but include live and resurrected owners."""
import os
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python
from tests.python.process_timeout import run_process_group_timeout


PROGRAM = '''import gc
import threading
import weakref

marker = []
live_holder = [marker]
saved = []
dead_refs = []


class Discarded:
    def __init__(self):
        self.loop = self
        self.marker = marker


class Revived:
    def __init__(self):
        self.loop = self

    def __del__(self):
        saved.append(self)


def create_cycles():
    gone = Discarded()
    dead_refs.append(weakref.ref(gone))
    revived = Revived()


def contains_identity(values, target):
    for value in values:
        if value is target:
            return True
    return False


def main():
    gc.disable()
    creator = threading.Thread(target=create_cycles)
    creator.start()
    creator.join()
    gc.collect()
    assert dead_refs[0]() is None
    assert len(saved) == 1
    objects = gc.get_objects()
    marker_refs = gc.get_referrers(marker)
    revived_refs = gc.get_referrers(saved[0])
    for obj in objects:
        assert not isinstance(obj, Discarded)
    for referrer in marker_refs:
        assert not isinstance(referrer, Discarded)
    assert contains_identity(objects, saved[0])
    assert contains_identity(objects, live_holder)
    assert contains_identity(marker_refs, live_holder)
    assert contains_identity(revived_refs, saved)
    print("dead hidden; live and resurrected visible")


main()
'''



@pytest.mark.integration
def test_public_snapshot_after_death_and_resurrection(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "lifecycle.py"
    source.write_text(PROGRAM)
    oracle = run_process_group_timeout([sys.executable, str(source)], timeout=30)
    assert (oracle.returncode, oracle.stdout, oracle.stderr) == (
        0, "dead hidden; live and resurrected visible\n", "",
    )
    binary = tmp_path / "lifecycle"
    compile_python(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    result = run_process_group_timeout(
        [str(binary)], timeout=30,
        env=dict(os.environ, PATH="/nonexistent", PCC_THREADS="1", PCC_REFCOUNT_KIND="atomic"),
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, oracle.stdout, "")
