"""Public collection preserves live generator frames and releases dead ones."""
import subprocess
import sys
import textwrap

import pytest

from tests.python.owned_regression_support import assert_owned_program, explicit_owned_runtime

PROGRAM = textwrap.dedent("""\
    import gc
    import weakref

    finalized = []
    closed = []

    class Payload:
        def __init__(self, ident):
            self.ident = ident
        def __del__(self):
            finalized.append(self.ident)

    def values(payload):
        try:
            yield payload.ident
        finally:
            closed.append(payload.ident)

    def discard(started):
        refs = []
        for i in range(777):
            payload = Payload(i)
            refs.append(weakref.ref(payload))
            gen = values(payload)
            if started:
                assert next(gen) == i
            gen = None
            payload = None
        return refs

    def main():
        gc.disable()
        sentinel = Payload(-1)
        live = values(sentinel)
        live_ref = weakref.ref(sentinel)
        sentinel = None
        assert next(live) == -1
        refs = discard(False)
        gc.collect()
        assert sorted(finalized) == list(range(777))
        assert closed == []
        for ref in refs:
            assert ref() is None
        finalized.clear()
        refs = discard(True)
        gc.collect()
        assert sorted(finalized) == list(range(777))
        assert sorted(closed) == list(range(777))
        for ref in refs:
            assert ref() is None
        assert live_ref() is not None
        gc.collect()
        assert len(finalized) == 777 and len(closed) == 777
        live.close()
        assert closed[-1] == -1
        print("GENERATOR_COLLECTION_OK")

    main()
""")
EXPECTED = "GENERATOR_COLLECTION_OK\n"


def test_public_collect_retires_ordinary_generators_cpython():
    run = subprocess.run([sys.executable, "-c", PROGRAM],
                         capture_output=True, text=True, timeout=20)
    assert (run.returncode, run.stdout, run.stderr) == (0, EXPECTED, ""), run


@pytest.mark.integration
@pytest.mark.parametrize("python_program_compiler", ("pcc0", "pcc1"), indirect=True)
def test_public_collect_retires_ordinary_generators(
    python_program_compiler, request, explicit_owned_runtime, tmp_path, capfd,
):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, EXPECTED, tmp_path, python_program_compiler,
                         mode, explicit_owned_runtime, capfd)
