#!/usr/bin/env python3
"""On-CPU and heap flame graphs for a running pcc/pcc1 process.

Why a flame graph and not the flat profile
------------------------------------------
`scripts/pcc_profile.py` ranks self time per function, which answers "what is
hot". It cannot answer "who called it", and for pcc1 that is the question that
matters: the flat profile of a large emit is ~49% GC pointer bookkeeping
(`pcc_gc_managed_pointer_find_slot`, the minor-graph lock, incref/decref)
spread across every caller in the compiler. Optimising the leaf is how you
measure 1.6x and ship nothing; the win is in whichever caller asks the question
too often. A flame graph attributes the same samples by call path.

Both modes read a tree from an Apple tool and fold it the same way:

    cpu   sample <pid> <secs>          -- on-CPU time
    heap  malloc_history <pid> -callTree  -- allocated bytes by call path
    peak  malloc_history <pid> -callTree -highWaterMark  -- live at peak RSS

`heap`/`peak` need the target launched with ``MallocStackLogging=1``. They see
system allocator buffers, often arena refills rather than individual pcc objects.

Symbol handling is the same discipline as pcc_profile.py, for the same reason:
symbols come from the sampled process's own executable, the slide is derived
from the image the tool reports rather than assumed, and frames from other
images are kept as-is instead of being resolved into fiction.

Usage
-----
    scripts/pcc_flamegraph.py cpu  <pid> [seconds] [-o out.svg] [--folded f.txt] [--exact-pid]
    scripts/pcc_flamegraph.py heap <pid> [-o out.svg]
    scripts/pcc_flamegraph.py peak <pid> [-o out.svg]
    scripts/pcc_flamegraph.py report --input-folded run.folded --focus SYMBOL --report-json new.json

Capture modes preserve raw evidence in a printed directory; select a new one
with --evidence-dir. Reports describe captured weights, not whole-build wall
time or correctness. See docs/knowledge/profiling-hotspots.md for guarded replay.
"""

from __future__ import annotations

import argparse
import collections
import html
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pcc_profile import (  # noqa: E402  (path set above)
    _busiest_leaf,
    call_graph_section,
    _executable,
    _resolve,
    _run,
    _symbols,
    _text_vmaddr,
)


def _read_folded(path: Path) -> collections.Counter:
    folded = collections.Counter()
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        stack, separator, count = line.rpartition(" ")
        if not separator or not stack or any(not f for f in stack.split(";")):
            raise ValueError(f"{path}:{number}: invalid folded stack")
        try:
            weight = int(count)
        except ValueError as exc:
            raise ValueError(f"{path}:{number}: invalid weight") from exc
        if weight < 0:
            raise ValueError(f"{path}:{number}: negative weight")
        folded[stack] += weight
    if not sum(folded.values()):
        raise ValueError(f"{path}: no positive samples")
    return folded


def _attribution(folded, focus=None, *, units="samples") -> dict:
    """Attribute each sample once; recursive inclusive frames count once too."""
    total = sum(folded.values())
    inclusive = collections.Counter()
    leaf = collections.Counter()
    for stack, weight in folded.items():
        frames = stack.split(";")
        leaf[frames[-1]] += weight
        for frame in set(frames):
            inclusive[frame] += weight
    resolved = None
    if focus:
        matches = [f for f in inclusive if f == focus]
        if not matches:
            matches = sorted(f for f in inclusive if focus in f)
        if len(matches) != 1:
            raise ValueError(f"focus {focus!r} needs one match, found {len(matches)}: {matches[:12]}")
        resolved = matches[0]
    children = collections.Counter()
    callers = collections.Counter()
    if resolved:
        for stack, weight in folded.items():
            frames = stack.split(";")
            if resolved not in frames:
                continue
            index = frames.index(resolved)
            children[frames[index + 1] if index + 1 < len(frames) else "[self]"] += weight
            callers[frames[index - 1] if index else "[root]"] += weight
    selected = inclusive[resolved] if resolved else total

    def rows(counts, denominator):
        return [{"symbol": symbol, "weight": count,
                 "percent_total": 100.0 * count / total if total else 0.0,
                 "percent_selected": 100.0 * count / denominator if denominator else 0.0}
                for symbol, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))]

    return {"schema": "pcc.hotspots.v1", "units": units, "total_weight": total,
            "scope": "captured stacks only; not whole-build wall time",
            "correctness": "NOT_CHECKED", "self": rows(leaf, total),
            "inclusive": rows(inclusive, total), "inclusive_overlaps": True,
            "focus": resolved, "selected_weight": selected,
            "direct_children": rows(children, selected), "callers": rows(callers, selected),
            "partition_rule": "first occurrence of focus per stack; children are disjoint"}


def _write_summary(args, folded, *, units="samples", metadata=None):
    report = _attribution(folded, args.focus, units=units)
    report["capture"] = metadata or {}
    if args.report_json:
        with open(args.report_json, "x") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
    key = "direct_children" if report["focus"] else "self"
    print("\n" + ("direct children of " + report["focus"] if report["focus"] else "aggregated self")
          + " (captured " + units + "):")
    for row in report[key][:args.top]:
        print(f"{row['weight']:>9} {row['percent_total']:6.2f}% total "
              f"{row['percent_selected']:6.2f}% selected  {row['symbol']}")
    return report


def _evidence_dir(args) -> Path:
    if args.evidence_dir:
        root = Path(args.evidence_dir)
        root.mkdir(parents=True, exist_ok=False)
    else:
        root = Path(tempfile.mkdtemp(prefix="pcc-profile-"))
    print(f"evidence {root}", flush=True)
    return root


def _capture_receipt(root, **fields):
    (root / "capture.json").write_text(json.dumps(fields, indent=2, sort_keys=True) + "\n")

# ``   +   ! :   | + 4468 ???  (in pcc1)  load address 0x... + 0x...``
_ROW = re.compile(
    r"^(?P<lead>[\s+!:|]*)(?P<count>\d+)(?:\s+\((?P<amount>[^)]*)\))?\s+(?P<rest>\S.*)$"
)
_ADDR = re.compile(r"load address 0x(?P<load>[0-9a-f]+) \+ 0x(?P<off>[0-9a-f]+)")
_IN_IMAGE = re.compile(r"\(in ([^)]+)\)")
_IMAGE_ROW = re.compile(r"^\s*0x([0-9a-f]+)\s+-\s+0x[0-9a-f]+\s+\+?(\S+)")
_ALLOC_AMOUNT = re.compile(
    r"^(?P<number>\d+(?:\.\d+)?)(?:(?P<unit>[BKMGTP])|\s+bytes?)?$"
)
_ALLOC_UNIT_BYTES = {
    "": 1,
    "B": 1,
    "K": 1024,
    "M": 1024**2,
    "G": 1024**3,
    "T": 1024**4,
    "P": 1024**5,
}


def _binary_identity(path: Path) -> tuple[int, int, int, int, int]:
    try:
        info = path.stat()
    except OSError as exc:
        raise SystemExit(f"executable not found: {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise SystemExit(f"executable is not a regular file: {path}")
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _image_load_address(text: str, image: str):
    for line in text.splitlines():
        row = _IMAGE_ROW.match(line)
        if row and row.group(2) == image:
            return int(row.group(1), 16)
    return None


def _allocation_amount_bytes(raw: str | None) -> int:
    if raw is None:
        raise ValueError("malloc_history call-tree row has no byte amount")
    match = _ALLOC_AMOUNT.fullmatch(raw.strip())
    if match is None:
        raise ValueError(
            "unsupported malloc_history byte amount: " + repr(raw)
        )
    number = float(match.group("number"))
    return int(number * _ALLOC_UNIT_BYTES[match.group("unit") or ""] + 0.5)


def _fold(
    text: str,
    image: str,
    symbols,
    slide: int,
    *,
    allocation_bytes: bool = False,
) -> collections.Counter:
    """Tree -> ``root;caller;callee <self weight>``.

    Weights in these tools are tree-cumulative, so a frame's own weight is its
    count minus the counts of its direct children -- the rows at the first
    deeper indent level, up to the next row at or above its own level.
    """
    rows = []
    for line in call_graph_section(text).splitlines():
        match = _ROW.match(line)
        if not match:
            rows.append(None)
            continue
        rest = match.group("rest")
        in_image = _IN_IMAGE.search(rest)
        addr = _ADDR.search(rest)
        if addr and in_image and in_image.group(1) == image:
            static = int(addr.group("load"), 16) + int(addr.group("off"), 16) - slide
            label, _ = _resolve(symbols, static)
        elif in_image:
            # Another image: keep the tool's own label rather than resolving it
            # against symbols it does not belong to.
            label = rest.split("(in ")[0].strip() or "???"
            label = label + " [" + in_image.group(1) + "]"
        else:
            label = rest.strip()[:80]
        weight = (
            _allocation_amount_bytes(match.group("amount"))
            if allocation_bytes
            else int(match.group("count"))
        )
        rows.append((len(match.group("lead")), weight, label))

    folded: collections.Counter = collections.Counter()
    stack: list[tuple[int, str]] = []
    for index, row in enumerate(rows):
        if row is None:
            continue
        depth, count, label = row
        while stack and stack[-1][0] >= depth:
            stack.pop()
        stack.append((depth, label))
        child_depth = None
        children = 0
        for follow in rows[index + 1 :]:
            if follow is None:
                continue
            if follow[0] <= depth:
                break
            if child_depth is None:
                child_depth = follow[0]
            if follow[0] == child_depth:
                children += follow[1]
        own = max(0, count - children)
        # Same on-CPU rule the injected host sampler uses: a frame blocked in
        # waitpid/sleep/condvar burns no CPU, and counting it makes the two
        # graphs incomparable -- which is the only reason to draw them.
        if own > 0 and any(b in label for b in _BLOCKED_NATIVE):
            own = 0
        if own > 0:
            folded[";".join(name for _, name in stack)] += own
    return folded


_BLOCKED_NATIVE = (
    "platform_waitpid",
    "platform_sleep_ns",
    "__psynch_cvwait",
    "__semwait_signal",
    "mach_msg",
    "kevent",
    "__select",
    "__wait4",
    "nanosleep",
)


_PALETTE = (
    "#e8663a", "#e8853a", "#e8a33a", "#e8c23a", "#d9e83a",
    "#a3e83a", "#e84f3a", "#e8703a", "#e8913a", "#e8b23a",
)


def _svg(folded: collections.Counter, title: str, subtitle: str) -> str:
    """Self-contained flame graph. No external flamegraph.pl, no CDN."""
    root: dict = {"name": "all", "value": 0, "children": {}}
    for path, weight in folded.items():
        node = root
        node["value"] += weight
        for name in path.split(";"):
            node = node["children"].setdefault(
                name, {"name": name, "value": 0, "children": {}}
            )
            node["value"] += weight
    total = root["value"] or 1

    width, row_height, pad_top = 1200, 17.0, 54.0
    boxes: list[tuple[float, float, float, str, int]] = []

    def walk(node, depth: float, offset: float) -> None:
        span = width * node["value"] / total
        boxes.append((offset, depth, span, node["name"], node["value"]))
        cursor = offset
        for child in sorted(
            node["children"].values(), key=lambda item: -item["value"]
        ):
            walk(child, depth + 1, cursor)
            cursor += width * child["value"] / total

    walk(root, 0, 0.0)
    max_depth = max(int(box[1]) for box in boxes) + 1
    height = pad_top + max_depth * row_height + 12

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height:.0f}" viewBox="0 0 {width} {height:.0f}" '
        f'font-family="ui-monospace, Menlo, monospace" font-size="11">',
        f'<rect width="{width}" height="{height:.0f}" fill="#f7f4ef"/>',
        f'<text x="{width / 2}" y="22" text-anchor="middle" font-size="15" '
        f'font-weight="600">{html.escape(title)}</text>',
        f'<text x="{width / 2}" y="40" text-anchor="middle" fill="#665e54">'
        f"{html.escape(subtitle)}</text>",
    ]
    for offset, depth, span, name, value in boxes:
        if span < 0.12:  # narrower than a hairline: not readable, not useful
            continue
        y = pad_top + (max_depth - 1 - depth) * row_height
        colour = _PALETTE[(hash(name) if name != "all" else 0) % len(_PALETTE)]
        share = 100.0 * value / total
        out.append(
            f'<g><title>{html.escape(name)} — {value} ({share:.2f}%)</title>'
            f'<rect x="{offset:.2f}" y="{y:.1f}" width="{max(span - 0.6, 0.4):.2f}" '
            f'height="{row_height - 1:.1f}" fill="{colour}" stroke="#f7f4ef" '
            f'stroke-width="0.4" rx="1"/>'
        )
        if span > 42:
            label = name if len(name) * 6.0 < span - 8 else name[: int(span / 6.0)]
            out.append(
                f'<text x="{offset + 3:.2f}" y="{y + row_height - 5:.1f}" '
                f'fill="#22201d">{html.escape(label)}</text>'
            )
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out)


_SITECUSTOMIZE = """
# Injected by scripts/pcc_flamegraph.py. Every Python process started with this
# directory on PYTHONPATH profiles ITSELF and writes folded stacks on exit, so
# the coordinator and every worker are all measured while running normally.
# Profiling the coordinator alone shows 95% `subprocess._try_wait` -- true and
# useless, because the work is in the children; and forcing the build serial so
# the main thread does the work would be making the measurement fit the tool.
import atexit, os, sys, threading, time

_BLOCKED = frozenset((
    "_try_wait [subprocess.py]",
    "_wait [subprocess.py]",
    "wait [subprocess.py]",
    "select [selectors.py]",
    "poll [selectors.py]",
    "_communicate [subprocess.py]",
    "read [<frozen os>]",
    "acquire [threading.py]",
))

_OUT = os.environ.get("PCC_FLAME_DIR", "")
if _OUT:
    _folded = {}
    _stop = threading.Event()
    _main = threading.get_ident()

    def _sampler():
        period = 1.0 / float(os.environ.get("PCC_FLAME_HZ", "200"))
        frames = sys._current_frames
        while not _stop.is_set():
            f = frames().get(_main)
            stack = []
            while f is not None and len(stack) < 220:
                c = f.f_code
                stack.append(c.co_name + " [" + os.path.basename(c.co_filename) + "]")
                f = f.f_back
            # On-CPU means on-CPU.  A coordinator blocked in waitpid/select
            # burns no CPU, but it exists for the whole build, so counting its
            # blocked samples alongside the workers' working samples sums
            # wall-clock across processes and makes every percentage
            # meaningless -- one run read 65% "_try_wait".
            if stack and stack[0] not in _BLOCKED:
                k = ";".join(reversed(stack))
                _folded[k] = _folded.get(k, 0) + 1
            time.sleep(period)

    def _finish():
        _stop.set()
        if not _folded:
            return
        try:
            path = os.path.join(_OUT, "flame-%d.folded" % os.getpid())
            with open(path, "w") as fh:
                for k, v in _folded.items():
                    fh.write(k + " " + str(v) + "\\n")
        except OSError:
            pass

    def _finish_memory():
        import tracemalloc

        if not tracemalloc.is_tracing():
            return
        snap = tracemalloc.take_snapshot()
        rows = []
        for stat in snap.statistics("traceback"):
            frames_ = []
            for fr in reversed(stat.traceback):
                frames_.append(
                    os.path.basename(fr.filename) + ":" + str(fr.lineno)
                )
            if frames_:
                rows.append((";".join(frames_), stat.size))
        if not rows:
            return
        try:
            path = os.path.join(_OUT, "mem-%d.folded" % os.getpid())
            with open(path, "w") as fh:
                for k, v in rows:
                    fh.write(k + " " + str(v) + "\\n")
        except OSError:
            pass

    if os.environ.get("PCC_FLAME_MEMORY"):
        import tracemalloc

        tracemalloc.start(int(os.environ.get("PCC_FLAME_MEMORY_DEPTH", "24")))
        atexit.register(_finish_memory)

    atexit.register(_finish)
    threading.Thread(target=_sampler, daemon=True).start()
"""


def _host_main(args) -> int:
    """Profile a whole host-pcc build, coordinator and workers alike."""
    if not args.argv:
        raise SystemExit("host mode needs --argv <pcc command line>")
    import shutil

    evidence = _evidence_dir(args)
    out_dir = str(evidence)
    inject = tempfile.mkdtemp(prefix="pcc_flame_inject_")
    Path(inject, "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
    env = dict(os.environ)
    env["PCC_FLAME_DIR"] = out_dir
    if getattr(args, "memory", False):
        env["PCC_FLAME_MEMORY"] = "1"
    env["PYTHONPATH"] = inject + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("LC_ALL", None)

    argv = list(args.argv)
    print(f"profiling host pcc (coordinator + workers): {' '.join(argv)}", flush=True)
    try:
        # `--cmd` profiles an arbitrary command instead of `python -m pcc`.
        # The sampler is injected through PYTHONPATH, so EVERY python process
        # the command spawns self-profiles -- which is how a build driven by a
        # shell script gets covered, including helper processes nobody thought
        # to instrument (scripts/pcc_link_macho.py turned out to be a
        # single-threaded 98%-CPU step inside the bootstrap).
        if args.cmd:
            command = argv
        else:
            command = [sys.executable, "-m", "pcc", *argv]
        _capture_receipt(evidence, command=command, status="RUNNING", correctness="NOT_CHECKED")
        try:
            done = subprocess.run(
                command, env=env, capture_output=True, text=True, timeout=7200,
            )
        except subprocess.TimeoutExpired as exc:
            for name, data in (("stdout", exc.stdout), ("stderr", exc.stderr)):
                (evidence / name).write_bytes(data.encode() if isinstance(data, str) else data or b"")
            _capture_receipt(evidence, command=command, status="TIMEOUT", correctness="NOT_CHECKED")
            raise
        (evidence / "stdout").write_text(done.stdout)
        (evidence / "stderr").write_text(done.stderr)
        metadata = {"command": command, "status": "EXITED" if done.returncode == 0 else "FAILED",
                    "returncode": done.returncode, "correctness": "NOT_CHECKED"}
        _capture_receipt(evidence, **metadata)
        if done.returncode != 0:
            print(done.stdout[-1500:] + done.stderr[-1500:])
            print(f"host pcc failed (exit {done.returncode}); preserving partial profile", file=sys.stderr)

        folded: collections.Counter = collections.Counter()
        pattern = "mem-*.folded" if getattr(args, "memory", False) else "flame-*.folded"
        files = sorted(Path(out_dir).glob(pattern))
        print(f"processes that wrote samples: {len(files)}", flush=True)
        if not files and done.stderr.strip():
            # site.py swallows sitecustomize failures; surface them instead of
            # reporting an empty profile as if nothing had run.
            print("child stderr:\n" + done.stderr.strip()[-1200:])
        for f in files:
            for line in f.read_text(encoding="utf-8").splitlines():
                path, _, weight = line.rpartition(" ")
                if path:
                    folded[path] += int(weight)
    finally:
        shutil.rmtree(inject, ignore_errors=True)

    if not folded:
        raise SystemExit(
            "no samples collected: the injected sampler wrote nothing. Check "
            "that PYTHONPATH reached the child (a wrapper may reset it)."
        )
    total = sum(folded.values())
    if args.folded:
        with open(args.folded, "w", encoding="utf-8") as stream:
            for path, weight in folded.most_common():
                stream.write(f"{path} {weight}\n")
        print(f"folded  {args.folded}")
    out = args.out or "pcc-host-flamegraph.svg"
    Path(out).write_text(
        _svg(folded,
             "pcc0 allocation flame graph" if getattr(args, "memory", False)
             else "pcc0 (host CPython) on-CPU flame graph",
             f"{len(files)} processes · {total} "
             + ("bytes" if getattr(args, "memory", False) else "samples")),
        encoding="utf-8",
    )
    print(f"svg     {out}   ({total} samples)")
    print("\ntop call paths by self samples:\n")
    for path, weight in folded.most_common(14):
        frames = path.split(";")
        leaf = frames[-1]
        caller = frames[-2] if len(frames) > 1 else ""
        print(f"{weight:>7}  {100.0 * weight / total:5.1f}%  {leaf[:42]:<42} <- {caller[:30]}")
    metadata["process_sample_files"] = [str(f) for f in files]
    _write_summary(args, folded, units="bytes" if args.memory else "samples", metadata=metadata)
    return 0 if done.returncode == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("cpu", "heap", "peak", "host", "report"))
    parser.add_argument("pid", type=int, nargs="?", default=0)
    parser.add_argument("seconds", nargs="?", type=int, default=10)
    parser.add_argument("-o", "--out", default=None)
    parser.add_argument("--folded", default=None)
    parser.add_argument("--input-folded", type=Path, help="report mode: re-analyze an existing capture")
    parser.add_argument("--units", choices=("samples", "bytes"), default="samples",
                        help="report mode: units of the supplied folded file")
    parser.add_argument("--focus", help="exact symbol or unique substring; report disjoint direct children")
    parser.add_argument("--report-json", help="new JSON file with full names and attribution denominators")
    parser.add_argument("--evidence-dir", help="new directory for raw capture, stderr and receipt")
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--exact-pid", action="store_true",
                        help="profile the supplied native PID, including a coordinator, without following children")
    parser.add_argument("--cmd", action="store_true",
                        help="host mode: --argv is a full command, not pcc args")
    parser.add_argument("--memory", action="store_true",
                        help="host mode: allocated bytes by Python traceback")
    # `--argv` is split off by hand: argparse.REMAINDER still tries to bind a
    # leading option-looking token to `pid`, which turns `--argv --backend
    # self` into "invalid int value: 'self'".
    raw = sys.argv[1:]
    if "--argv" in raw:
        cut = raw.index("--argv")
        args = parser.parse_args(raw[:cut])
        args.argv = [a for a in raw[cut + 1 :] if a != "--"]
    else:
        args = parser.parse_args(raw)
        args.argv = []

    if args.report_json and Path(args.report_json).exists():
        parser.error("--report-json already exists; preserve prior evidence")
    if args.mode == "report":
        if args.input_folded is None or args.exact_pid:
            parser.error("report requires --input-folded and does not accept --exact-pid")
        try:
            folded = _read_folded(args.input_folded)
            _write_summary(args, folded, units=args.units, metadata={
                "path": str(args.input_folded.resolve()),
                "sha256": hashlib.sha256(args.input_folded.read_bytes()).hexdigest(),
                "status": "OFFLINE", "workload_status": "UNKNOWN",
            })
        except ValueError as exc:
            parser.error(str(exc))
        if args.out:
            with open(args.out, "x") as stream:
                stream.write(_svg(folded, "pcc captured stacks", str(args.input_folded)))
        return 0
    if args.input_folded:
        parser.error("--input-folded requires report mode")

    if args.mode == "host":
        if args.exact_pid:
            parser.error("--exact-pid requires a native cpu/heap/peak mode")
        return _host_main(args)

    if args.exact_pid and args.pid <= 0:
        parser.error("--exact-pid requires a positive PID")
    pid = args.pid if args.exact_pid else _busiest_leaf(args.pid)
    binary = _executable(pid)
    if not binary.is_absolute():
        binary = (Path.cwd() / binary).resolve()
    binary_identity = _binary_identity(binary)
    evidence = _evidence_dir(args)
    metadata = {"mode": args.mode, "pid": pid, "requested_pid": args.pid,
                "binary": str(binary), "binary_identity": binary_identity,
                "requested_seconds": args.seconds, "status": "CAPTURING",
                "workload_status": "UNKNOWN", "correctness": "NOT_CHECKED"}
    _capture_receipt(evidence, **metadata)
    partial_capture = None

    if args.mode == "cpu":
        print(f"sampling pid {pid} for {args.seconds}s", flush=True)
        with tempfile.NamedTemporaryFile(suffix=".sample", delete=False) as handle:
            path = handle.name
        try:
            done = subprocess.run(
                [
                    "sample",
                    str(pid),
                    str(args.seconds),
                    "-mayDie",
                    "-file",
                    path,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            text = Path(path).read_text(errors="replace")
            (evidence / "native.sample").write_text(text)
            (evidence / "stderr").write_text(done.stderr)
            metadata.update(status="CAPTURED_UNVALIDATED", capture_returncode=done.returncode)
            _capture_receipt(evidence, **metadata)
        finally:
            os.unlink(path)
        if not text.strip():
            detail = done.stderr.strip() or done.stdout.strip()
            raise SystemExit(
                f"sample failed for pid {pid} (exit {done.returncode})"
                + (f": {detail[:400]}" if detail else "")
            )
        if done.returncode != 0:
            detail = done.stderr.strip() or done.stdout.strip()
            partial_capture = (
                f"sample exited {done.returncode} after writing a report"
                + (f": {detail[:400]}" if detail else "")
            )
        units = "samples"
    else:
        argv = ["malloc_history", str(pid), "-callTree", "-collapseRecursion"]
        if args.mode == "peak":
            argv.append("-highWaterMark")
        print(f"reading allocation stacks from pid {pid}", flush=True)
        done = subprocess.run(argv, capture_output=True, text=True, check=False)
        text = done.stdout
        (evidence / "native.sample").write_text(text)
        (evidence / "stderr").write_text(done.stderr)
        metadata.update(status="CAPTURED_UNVALIDATED", capture_returncode=done.returncode)
        _capture_receipt(evidence, **metadata)
        if "MallocStackLogging" in done.stderr or not text.strip():
            raise SystemExit(
                "no allocation stacks: relaunch the target with "
                "MallocStackLogging=1 in its environment.\n"
                f"{done.stderr.strip()[:400]}"
            )
        units = "bytes"

    # Capture first.  `nm` and symbol-table parsing can take a material part of
    # a short native worker's lifetime; doing them before `sample` silently
    # omitted parse/prepare and made the resulting Amdahl attribution wrong.
    # The executable path is resolved above while the process is alive, then
    # all post-processing happens after the raw call tree is safely captured.
    if _binary_identity(binary) != binary_identity:
        raise SystemExit(
            f"executable changed while capturing pid {pid}: {binary}; "
            "refusing to resolve old samples with new symbols"
        )
    image = binary.name
    load = _image_load_address(text, image)
    if load is None:
        raise SystemExit(
            f"the captured report has no image named {image!r}; pid {pid} "
            "exited before a usable report or the selected process changed"
        )
    symbols = _symbols(binary)
    if not symbols:
        raise SystemExit(f"{binary} has no text symbols; attribution is meaningless")
    stamp = _run(["stat", "-f", "%Sm", "-t", "%Y-%m-%d %H:%M:%S", str(binary)]).strip()
    print(f"binary  {binary}")
    print(f"built   {stamp}   text symbols {len(symbols)}")

    slide = load - _text_vmaddr(binary)

    folded = _fold(
        text,
        image,
        symbols,
        slide,
        allocation_bytes=args.mode != "cpu",
    )
    if not folded:
        raise SystemExit(f"no stacks collected from pid {pid}")
    total = sum(folded.values())
    metadata.update(status="PARTIAL" if partial_capture else "CAPTURED", units=units,
                    binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
    _capture_receipt(evidence, **metadata)
    (evidence / "capture.folded").write_text("".join(f"{s} {w}\n" for s, w in folded.most_common()))
    _write_summary(args, folded, units=units, metadata=metadata)
    if partial_capture is not None:
        print(
            "warning: partial CPU capture accepted because its image and "
            f"stacks are valid; {partial_capture}",
            file=sys.stderr,
        )

    if args.folded:
        with open(args.folded, "w", encoding="utf-8") as stream:
            for path, weight in folded.most_common():
                stream.write(f"{path} {weight}\n")
        print(f"folded  {args.folded}")

    out = args.out or f"pcc-{args.mode}-flamegraph.svg"
    title = {
        "cpu": "pcc on-CPU flame graph",
        "heap": "pcc allocation flame graph",
        "peak": "pcc peak-live allocation flame graph",
    }[args.mode]
    subtitle = f"{binary.name} · pid {pid} · {total} {units} · built {stamp}"
    Path(out).write_text(_svg(folded, title, subtitle), encoding="utf-8")
    print(f"svg     {out}   ({total} {units})")

    print(f"\ntop call paths by self {units}:\n")
    for path, weight in folded.most_common(12):
        leaf = path.rsplit(";", 1)[-1]
        caller = path.split(";")[-2] if ";" in path else ""
        print(f"{weight:>9}  {100.0 * weight / total:5.1f}%  {leaf[:44]:<44} <- {caller[:28]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
