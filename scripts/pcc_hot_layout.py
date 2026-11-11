#!/usr/bin/env python3
"""Cache-line audit of the runtime's hot shared words in a linked executable.

The linker packs globals in definition order, and where a module's data
begins depends on the size of every module linked before it. A change to
unrelated code can therefore move a word that every thread writes onto the
cache line of a word that every thread reads, and a multi-worker benchmark
then moves by several percent although no path it runs has changed. This
tool answers in seconds, from the symbol table alone, what otherwise takes a
build and a paired measurement per guess:

    uv run python scripts/pcc_hot_layout.py BINARY
        every hot word, its offset in its line and the symbols sharing it;
        exit status 1 when a written word shares a line with a read word
        or with a written word of another group (--check).

    uv run python scripts/pcc_hot_layout.py BINARY --diff OTHER
        the hot words whose line neighbours differ between two builds.

A word is listed in HOT_WORDS with a role: "written" for a shared word that
running threads write on a hot path (lock words, counters, list heads),
"read" for one they read on a hot path and write only when a mode changes.
Words with the same group are written or read together and may share a
line. Padding symbols (``*_isolation_before`` / ``*_isolation_after``) and
symbols no code reads after constant folding do not count as neighbours.

Mach-O executables are read with pcc's own parser; no external tool runs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).absolute().parents[1]
CACHE_LINE = 128

# name -> (role, group). Extend this table when a profile shows a new word
# written or polled on a hot path; the linked test fails on a new conflict.
HOT_WORDS: dict[str, tuple[str, str]] = {
    # Written by running threads.
    "py_gc_tracked_count": ("written", "tracked-count"),
    "pcc_gc_tracked_after_collect": ("written", "tracked-count"),
    "pcc_gc_continuation_root_head": ("written", "root-registry"),
    "pcc_gc_scheduler_root_head": ("written", "root-registry"),
    "pcc_gc_root_registry_revision": ("written", "root-registry"),
    "pcc_allocator_lock": ("written", "allocator-lock"),
    "g_pcc_py_gc_minor_graph_lock": ("written", "graph-lock"),
    "pcc_gc_foreign_lease_active": ("written", "foreign-lease"),
    "pcc_gc_debt_bytes": ("written", "allocation-debt"),
    "pcc_gc_cms_queue_pushes": ("written", "allocation-debt"),
    "pcc_gc_live_bytes": ("written", "object-list"),
    "pcc_gc_object_head": ("written", "object-list"),
    "pcc_gc_object_node_free_head": ("written", "object-node-pool"),
    "pcc_gc_object_node_free_count": ("written", "object-node-pool"),
    "pcc_gc_frame_head": ("written", "frame-registry"),
    "pcc_parked_thread_count_py": ("written", "world-counters"),
    "pcc_exited_thread_count_py": ("written", "world-counters"),
    "pcc_live_thread_count_py": ("written", "world-counters"),
    "pcc_gc_alloc_stage_published": ("written", "alloc-stage-counters"),
    "pcc_gc_alloc_stage_full_drains": ("written", "alloc-stage-counters"),
    # Read by every thread on a hot path, written when a mode changes.
    "pcc_gc_backend_selected": ("read", "hot-config"),
    "pcc_gc_refcount_provenance_probe": ("read", "hot-config"),
    "pcc_gc_refcount_fast": ("read", "hot-config"),
    "pcc_gc_refcount_atomic": ("read", "hot-config"),
    "pcc_gc_backend4_no_refcounts": ("read", "hot-config"),
    "pcc_gc_config_initialized": ("read", "hot-config"),
    "pcc_gc_read_barrier_enabled": ("read", "hot-config"),
    "pcc_gc_backend0_frame_roots_enabled": ("read", "hot-config"),
    "pcc_gc_frame_registry_inactive": ("read", "hot-config"),
    "pcc_gc_backend_frozen": ("read", "mode-words"),
    "pcc_gc_list_container_mode": ("read", "mode-words"),
    "pcc_gc_nonmoving_count_free": ("read", "mode-words"),
    "pcc_gc_tracking_flag_mode": ("read", "mode-words"),
    "py_gc_enabled": ("read", "mode-words"),
    "py_gc_threshold0": ("read", "mode-words"),
    "pcc_gc_mark_active": ("read", "mark-phase"),
    "pcc_gc_forwarding_population": ("read", "forwarding"),
    "pcc_thread_stop_requested": ("read", "poll-flags"),
    "pcc_thread_world_stop_py": ("read", "poll-flags"),
    "pcc_gc_alloc_stage_enabled": ("read", "alloc-stage-mode"),
    "pcc_gc_stackmap_walk_state": ("read", "stackmap-mode"),
    "pcc_diagnostics_runtime_log_fast_state": ("read", "log-mode"),
}


def is_inert(name: str) -> bool:
    """A symbol that occupies bytes but is neither read nor written while
    the program runs: isolation padding, and a runtime port's module
    variables whose uses were folded to immediates."""
    if name.endswith("_isolation_before") or name.endswith("_isolation_after"):
        return True
    if name.startswith(".modvar.") and name.endswith("_initialized"):
        return True
    return False


def data_symbols(path: Path) -> list[tuple[int, str]]:
    """(address, name without the leading underscore) of every symbol the
    executable defines in a section, sorted by address."""
    sys.path.insert(0, str(REPO_ROOT))
    from pcc.backend.macho_spec import parse_object

    rows = parse_object(Path(path).read_bytes()).symbols()
    found = []
    for row in rows:
        # Section-defined, not a debug entry.
        if (row["n_type"] & 0xE0) != 0 or (row["n_type"] & 0x0E) != 0x0E:
            continue
        name = row["name"]
        if name.startswith("_"):
            name = name[1:]
        found.append((row["n_value"], name))
    found.sort()
    return found


def line_neighbours(
    symbols: list[tuple[int, str]], line: int = CACHE_LINE,
) -> dict[str, dict]:
    """For each hot word present: its offset in its line and the other
    symbols whose first byte or extent lies on that line."""
    addresses = [address for address, _ in symbols]
    by_name = {name: address for address, name in symbols}
    report: dict[str, dict] = {}
    for name, (role, group) in HOT_WORDS.items():
        if name not in by_name:
            continue
        address = by_name[name]
        start = address - address % line
        end = start + line
        mates = []
        for index, (other_address, other) in enumerate(symbols):
            if other == name:
                continue
            # A symbol extends to the next symbol's address.
            other_end = (
                addresses[index + 1] if index + 1 < len(symbols) else other_address + 8
            )
            if other_address < end and other_end > start:
                mates.append(other)
        report[name] = {
            "role": role,
            "group": group,
            "address": address,
            "offset": address % line,
            "neighbours": mates,
        }
    return report


def conflicts(report: dict[str, dict]) -> list[tuple[str, str, str]]:
    """(written word, neighbour, reason) for every line a written word shares
    with a hot word of another group."""
    found = []
    for name, entry in report.items():
        if entry["role"] != "written":
            continue
        for other in entry["neighbours"]:
            if is_inert(other) or other not in HOT_WORDS:
                continue
            other_role, other_group = HOT_WORDS[other]
            if other_group == entry["group"]:
                continue
            reason = (
                "every thread reads it" if other_role == "read"
                else "another written group"
            )
            found.append((name, other, reason))
    return found


def neighbour_changes(
    first: dict[str, dict], second: dict[str, dict],
) -> list[tuple[str, list[str], list[str]]]:
    """Hot words whose non-inert line neighbours differ between two builds."""
    changed = []
    for name in sorted(set(first) & set(second)):
        before = sorted(n for n in first[name]["neighbours"] if not is_inert(n))
        after = sorted(n for n in second[name]["neighbours"] if not is_inert(n))
        if before != after:
            changed.append((name, before, after))
    return changed


def _print_report(report: dict[str, dict]) -> None:
    for name in sorted(report, key=lambda n: report[n]["address"]):
        entry = report[name]
        live = [n for n in entry["neighbours"] if not is_inert(n)]
        print(
            f"{entry['role']:7s} {entry['group']:22s} +{entry['offset']:3d} {name}"
        )
        for other in live:
            mark = ""
            if other in HOT_WORDS and HOT_WORDS[other][1] != entry["group"]:
                mark = "   <-- hot (" + HOT_WORDS[other][0] + ")"
            print(f"            {other}{mark}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("binary", type=Path)
    parser.add_argument("--diff", type=Path, help="a second executable to compare")
    parser.add_argument("--line", type=int, default=CACHE_LINE)
    parser.add_argument("--check", action="store_true",
                        help="exit 1 when a written word shares a line with another hot group")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = line_neighbours(data_symbols(args.binary), args.line)
    found = conflicts(report)
    if args.diff is not None:
        other = line_neighbours(data_symbols(args.diff), args.line)
        changes = neighbour_changes(report, other)
        if args.json:
            print(json.dumps({"changes": changes}, indent=1))
        else:
            for name, before, after in changes:
                print(name)
                print("    " + str(args.binary) + ": " + ", ".join(before))
                print("    " + str(args.diff) + ": " + ", ".join(after))
            print(f"{len(changes)} hot words changed line neighbours")
        return 0
    if args.json:
        print(json.dumps({"words": report, "conflicts": found}, indent=1))
    else:
        _print_report(report)
        print()
        for name, other, reason in found:
            print(f"CONFLICT {name} shares a line with {other}: {reason}")
        print(f"{len(found)} conflicts, {len(report)} hot words present")
    if args.check and found:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
