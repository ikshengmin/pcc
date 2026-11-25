"""An attribute read logs its dispatch event only while the runtime log is on.

``py_obj_getattr`` makes the log's own first test inline (the
``pcc_diagnostics_runtime_log_fast_state`` word) and calls
``pcc_diagnostics_runtime_log_event_code`` only when the log is on. A
compiled program reads attributes of values the compiler does not type; with
``PCC_LOG=dispatch`` the log holds its ``getattr`` events, and without it the
program prints the same and writes no log.
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap

PROGRAM = textwrap.dedent("""
    class Box:
        def __init__(self, value):
            self.value = value


    def read(items, name):
        total = 0
        for item in items:
            total += getattr(item, name)
        return total


    def main():
        boxes = [Box(index) for index in range(10)]
        print(read(boxes, "value"))


    main()
    """).lstrip()


def test_getattr_dispatch_event_follows_the_runtime_log(tmp_path):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "getattr_log.py"
    executable = tmp_path / "getattr_log"
    source.write_text(PROGRAM, encoding="utf-8")
    compile_python(str(source), str(executable), ir_scaffold_mode="on", backend="self")

    log_path = tmp_path / "runtime.jsonl"
    logged_env = dict(os.environ, PCC_LOG="dispatch", PCC_LOG_FORMAT="json",
                      PCC_LOG_FILE=str(log_path))
    logged = subprocess.run([str(executable)], capture_output=True, text=True,
                            timeout=60, env=logged_env)
    assert (logged.returncode, logged.stdout) == (0, "45\n"), logged.stderr
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
              if line.strip()]
    getattrs = [event for event in events
                if event["category"] == "dispatch" and event["event"] == "getattr"]
    assert len(getattrs) >= 10

    quiet_log = tmp_path / "quiet.jsonl"
    quiet_env = {key: value for key, value in os.environ.items() if not key.startswith("PCC_LOG")}
    quiet = subprocess.run([str(executable)], capture_output=True, text=True,
                           timeout=60, env=quiet_env)
    assert (quiet.returncode, quiet.stdout) == (0, "45\n"), quiet.stderr
    assert not quiet_log.exists()
