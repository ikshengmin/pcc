from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path


def _compile_and_collect_events(tmp_path: Path, source: str):
    src = tmp_path / "probe.py"
    exe = tmp_path / "probe"
    src.write_text(
        textwrap.dedent(source),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    build = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--backend",
            "self",
            "--python-libpython=off",
            "--ir-scaffold=on",
            str(src),
            "-o",
            str(exe),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    log_path = tmp_path / "exception.jsonl"
    run_env = {
        **env,
        "PCC_GC_BACKEND": "0",
        "PCC_LOG": "exception,refcount",
        "PCC_LOG_FORMAT": "json",
        "PCC_LOG_FILE": str(log_path),
    }
    run = subprocess.run(
        [str(exe)],
        text=True,
        capture_output=True,
        timeout=30,
        env=run_env,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == "done"

    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _assert_exception_lifetimes(events, tag):
    # Cleanup/handler selection may temporarily clear and restore TLS. Match
    # each allocation through its actual raises, clears, free and deallocation,
    # rather than equating internal TLS transitions with source-level raises.
    active = {}
    completed = 0
    for event in events:
        category = event["category"]
        name = event["event"]
        if category == "refcount":
            if name != "free" or event["value1"] != 12:
                continue
        elif category != "exception":
            continue
        pointer = event["ptr"]
        if name == "alloc":
            assert pointer not in active, event
            active[pointer] = []
        assert pointer in active, event
        active[pointer].append(name)
        if name == "new":
            assert event["value0"] == tag
        if name == "dealloc":
            history = active.pop(pointer)
            assert history[:2] == ["alloc", "new"], history
            assert history[-2:] == ["free", "dealloc"], history
            transitions = history[2:-2]
            assert transitions, history
            assert transitions == ["raise", "clear"] * (len(transitions) // 2), history
            completed += 1
    assert not active, active
    assert completed == 50


def test_builtin_exception_raise_releases_temporary_after_clear(tmp_path: Path):
    events = _compile_and_collect_events(
        tmp_path,
        """
        def main():
            i = 0
            while i < 50:
                try:
                    raise AttributeError("x")
                except AttributeError:
                    pass
                i += 1
            print("done")
        main()
        """,
    )
    _assert_exception_lifetimes(events, 6)


def test_runtime_stopiteration_helper_releases_temporary_after_clear(
    tmp_path: Path,
):
    events = _compile_and_collect_events(
        tmp_path,
        """
        def main():
            i = 0
            while i < 50:
                it = iter([])
                try:
                    next(it)
                except StopIteration:
                    pass
                i += 1
            print("done")
        main()
        """,
    )
    _assert_exception_lifetimes(events, 8)


def test_runtime_attribute_error_helper_releases_temporary_after_clear(
    tmp_path: Path,
):
    events = _compile_and_collect_events(
        tmp_path,
        """
        class C:
            pass

        def main():
            obj = C()
            i = 0
            while i < 50:
                try:
                    obj.missing
                except AttributeError:
                    pass
                i += 1
            print("done")
        main()
        """,
    )
    _assert_exception_lifetimes(events, 6)
