"""Native OS thread collects while another thread produces/retains paths."""
import gc
import os
import threading
from pcc.extern import extern, c_int64

threads_enabled = extern("pcc_threads_enabled", (), c_int64)
backend = extern("pcc_gc_backend", (), c_int64)
EXPECTED_BACKEND = __EXPECTED_BACKEND__
ABS_CASES = __ABS_CASES__
NORM_CASES = __NORM_CASES__
REL_CASES = __REL_CASES__

ready = threading.Event()
requested = threading.Event()
collecting = threading.Event()
completed = threading.Event()
resume = threading.Event()
finished = threading.Event()
progress = [0, 0, 0]


def fresh(value: str) -> str:
    return (value + "#")[:-1]


def collector():
    progress[1] = threading.get_ident()
    ready.set()
    for round_index in range(12):
        requested.wait()
        requested.clear()
        collecting.set()
        for j in range(4):
            garbage = []
            for i in range(128):
                garbage.append(("收集线程" * 40) + str(i))
            gc.collect()
            assert len(garbage) == 128
            progress[0] = progress[0] + 1
        completed.set()
        resume.wait()
        resume.clear()
    progress[2] = 1
    finished.set()


def main():
    assert threads_enabled() == 1
    assert backend() == EXPECTED_BACKEND
    main_ident = threading.get_ident()
    worker = threading.Thread(target=collector)
    worker.start()
    ready.wait()
    assert progress[1] != main_ident, "collector did not run on a distinct OS thread"
    retained = []
    expected = []
    for round_index in range(12):
        # Keep results alive before allowing this round's background collection.
        path, answer = ABS_CASES[round_index % len(ABS_CASES)]
        retained.append(os.path.abspath(fresh(path)))
        expected.append(answer)
        path, answer = NORM_CASES[round_index % len(NORM_CASES)]
        retained.append(os.path.normpath(fresh(path)))
        expected.append(answer)
        requested.set()
        collecting.wait()
        collecting.clear()
        # Repeated calls race with the background allocation/collect loop.
        for i in range(4):
            path, start, answer = REL_CASES[(round_index + i) % len(REL_CASES)]
            retained.append(os.path.relpath(fresh(path), fresh(start)))
            expected.append(answer)
        completed.wait()
        completed.clear()
        assert progress[0] == (round_index + 1) * 4
        for i in range(len(expected)):
            assert retained[i] == expected[i]
        resume.set()
    finished.wait()
    worker.join()
    assert progress[2] == 1 and progress[0] == 48
    gc.collect()
    for i in range(len(expected)):
        assert retained[i] == expected[i]
    print("threaded-path-collector-ok")


main()
