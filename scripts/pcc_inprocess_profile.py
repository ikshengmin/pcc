"""Resolve PCs from the macOS ARM64 in-process pcc sampler.

Build scripts/pcc_inprocess_sampler.c with host clang as a diagnostic dylib,
then run an already-built native pcc1 with DYLD_INSERT_LIBRARIES and
PCC_THREAD_SAMPLE_FILE set. This does not participate in pcc1 compilation or
constitute an owned-toolchain build. Keep the native command under the usual
process-tree RSS/timeout/performance-lock guard.

The sampler suspends the process's original thread at intervals and reads its
Mach thread state. A separate thread avoids the syscall-return bias observed
with ITIMER_PROF/ITIMER_VIRTUAL signals. LR is a caller hint for leaf functions,
not a complete stack: a nested call can overwrite the register.
"""

from __future__ import annotations

import argparse
import bisect
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess


MAGIC = 0x5043435448524432
HEADER = struct.Struct("<QQQQ")
RECORD = struct.Struct("<QQQQQ")
TEXT_SYMBOL = re.compile(r"([0-9a-fA-F]+) [tT] (.+)")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def report(binary: Path, samples: Path, sampler_dylib: Path) -> dict:
    raw = samples.read_bytes()
    if len(raw) < HEADER.size or (len(raw) - HEADER.size) % RECORD.size:
        raise ValueError("invalid in-process sample length")
    magic, slide, pid, period_us = HEADER.unpack_from(raw)
    if magic != MAGIC or slide == (1 << 64) - 1:
        raise ValueError("invalid sampler version or executable slide")
    symbol_text = subprocess.check_output(
        ["nm", "-n", str(binary)], text=True, timeout=60,
    )
    symbols = []
    for line in symbol_text.splitlines():
        match = TEXT_SYMBOL.fullmatch(line)
        if match:
            symbols.append((int(match.group(1), 16), match.group(2)))
    symbols.sort()
    if not symbols:
        raise ValueError("binary has no text symbols")
    addresses = [address for address, _ in symbols]
    names = [name for _, name in symbols]

    def resolve(pc: int) -> str:
        address = pc - slide
        if pc == 0 or address < addresses[0] or address >= addresses[-1] + 65536:
            return "<outside main>"
        return names[bisect.bisect_right(addresses, address) - 1]

    states: Counter[int] = Counter()
    callees: Counter[str] = Counter()
    pairs: Counter[tuple[str, str]] = Counter()
    outside_pcs: Counter[int] = Counter()
    first_cpu = None
    last_cpu = None
    for pc, lr, state, user_us, system_us in RECORD.iter_unpack(raw[HEADER.size:]):
        states[state] += 1
        if first_cpu is None:
            first_cpu = (user_us, system_us)
        last_cpu = (user_us, system_us)
        if pc == 0:
            continue
        callee = resolve(pc)
        if callee == "<outside main>":
            outside_pcs[pc] += 1
            continue
        callees[callee] += 1
        pairs[(callee, resolve(lr))] += 1
    if first_cpu is None or last_cpu is None or not callees:
        raise ValueError("no running main-executable samples")
    active = sum(callees.values()) + sum(outside_pcs.values())
    return {
        "schema": "pcc.inprocess-thread-pcs.v1",
        "binary": str(binary), "binary_sha256": digest(binary),
        "sampler_dylib": str(sampler_dylib),
        "sampler_dylib_sha256": digest(sampler_dylib),
        "raw": str(samples), "raw_sha256": digest(samples),
        "pid": pid, "slide": slide, "period_us": period_us,
        "records": (len(raw) - HEADER.size) // RECORD.size,
        "states": {str(state): count for state, count in sorted(states.items())},
        "main_samples": sum(callees.values()),
        "outside_main_samples": sum(outside_pcs.values()),
        "cpu_delta_us": [last_cpu[0] - first_cpu[0],
                         last_cpu[1] - first_cpu[1]],
        "outside_pc_top": [
            {"pc": hex(pc), "samples": count}
            for pc, count in outside_pcs.most_common(12)
        ],
        "symbols": [
            {"name": name, "samples": count, "active_share": count / active}
            for name, count in callees.most_common()
        ],
        "lr_hints": [
            {"callee": callee, "lr": lr, "samples": count}
            for (callee, lr), count in pairs.most_common()
        ],
        "limits": "Main-thread self PCs only; nearest Mach-O text symbol; "
                  "LR is a hint, not an unwound call stack; diagnostic dylib "
                  "is external to the owned pcc1 toolchain.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--sampler-dylib", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-binary-sha256")
    parser.add_argument("--caller-for", action="append", default=[])
    args = parser.parse_args()
    result = report(args.binary.resolve(strict=True),
                    args.samples.resolve(strict=True),
                    args.sampler_dylib.resolve(strict=True))
    if (args.expected_binary_sha256 and
            result["binary_sha256"] != args.expected_binary_sha256):
        parser.error("sampled binary hash differs from the expected receipt")
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print("records", result["records"], "states", result["states"],
          "main", result["main_samples"],
          "outside", result["outside_main_samples"],
          "cpu_us", result["cpu_delta_us"])
    for row in result["symbols"][:30]:
        print(f"{row['samples']:6d} {row['active_share'] * 100:5.1f}% {row['name']}")
    for name in args.caller_for:
        print("LR hints for", name)
        rows = (row for row in result["lr_hints"] if row["callee"] == name)
        for row in list(rows)[:12]:
            print(f"{row['samples']:6d} {row['lr']}")
    print("receipt", args.output)


if __name__ == "__main__":
    main()
