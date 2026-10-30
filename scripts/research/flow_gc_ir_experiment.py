#!/usr/bin/env python3
"""Test-only Flow-inspired contracts over REAL emitted PCC shared-return IR.

The production emitter is unchanged. The inline control selects its existing
target=None cleanup branch, disabling only pinned suffix sharing. No native
program, collector, bootstrap, or optimizer pass is executed by this experiment.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import platform
import re
import statistics
import time

from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_parse import decode_ssa_name
from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module
from tests.owned_ir_validation import verify_ir_text
from tests.python.root_slot_contract import RootSlotContract


@dataclass(frozen=True)
class ReturnSnapshot:
    function: str
    entry: str
    head: str
    target: str
    slots: tuple[str, ...]
    rooted: bool


class ObservedCodegen(L1CodeGen):
    """Observe production selection, without replacing its IR or helpers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.flow_snapshots = []
        self.flow_rooted = False

    def _return_through_shared_exit(self, value, skip_name=None, rooted=False):
        previous = self.flow_rooted
        self.flow_rooted = rooted
        try:
            return super()._return_through_shared_exit(value, skip_name, rooted)
        finally:
            self.flow_rooted = previous

    def _shared_return_pinned_cleanup(self, slots, target):
        entry = self.builder.block.name
        head = super()._shared_return_pinned_cleanup(slots, target)
        self.flow_snapshots.append(ReturnSnapshot(
            self.current_function.name, entry, head.name, target.name,
            tuple(decode_ssa_name(str(slot)) for slot in slots), self.flow_rooted,
        ))
        return head


class _InlineCleanup:
    """The existing real inline path; shared frame-leave exits remain enabled."""

    def _emit_owned_local_cleanup(self, skip_name=None, skip_payload=None,
                                 shared_exit_leaves=False, shared_pinned_target=None):
        if shared_exit_leaves and shared_pinned_target is not None:
            result = super()._emit_owned_local_cleanup(
                skip_name, skip_payload, shared_exit_leaves, None,
            )
            assert result is None
            block = self.builder.block.name
            if hasattr(self, "flow_snapshots"):
                self.flow_snapshots.append(ReturnSnapshot(
                    self.current_function.name, block, block,
                    shared_pinned_target.name, (), self.flow_rooted,
                ))
            return shared_pinned_target
        return super()._emit_owned_local_cleanup(
            skip_name, skip_payload, shared_exit_leaves, shared_pinned_target,
        )


class InlineCodegen(_InlineCleanup, ObservedCodegen):
    pass


class InlineTimingCodegen(_InlineCleanup, L1CodeGen):
    """No observation hooks in the timed inline control."""


def source_program(roots, returns_per_root=2):
    if not 1 <= roots <= 16 or not 1 <= returns_per_root <= 4:
        raise ValueError("bounded experiment supports 1..16 roots and 1..4 returns per root")
    lines = ["import threading", ""]
    for name, annotation in (("probe", ""), ("void_probe", " -> None")):
        lines += [f"def {name}(xs: list[int], choice: int){annotation}:",
                  "    if choice < 0:", "        return None" if name == "probe" else "        return"]
        for index in range(roots):
            lines.append(f"    item_{index} = [*xs, {index}]")
            for branch in range(returns_per_root):
                lines += [f"    if choice == {index * returns_per_root + branch}:",
                          f"        return item_{index}" if name == "probe" else "        return"]
        lines += ["    return None" if name == "probe" else "    return", ""]
    return "\n".join(lines)


def emit(source, *, inline=False, observe=True):
    started = time.perf_counter()
    cpu_started = time.process_time()
    module = infer_module(parse_and_lift(source, "flow_experiment.py", "flow_experiment"))
    inferred = time.perf_counter()
    if observe:
        generator = InlineCodegen if inline else ObservedCodegen
    else:
        generator = InlineTimingCodegen if inline else L1CodeGen
    codegen = generator(module, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    # Public generate() includes textual rendering on this source revision.
    text = codegen.generate(module)
    emitted = time.perf_counter()
    assert isinstance(text, str), "measure the actual public generate text boundary"
    assert codegen._runtime_threads_enabled, "fixture must select persistent pinned temp roots"
    return text, tuple(getattr(codegen, "flow_snapshots", ())), {
        "lift_infer_seconds": inferred - started,
        "codegen_and_render_seconds": emitted - inferred,
        "host_frontend_seconds": emitted - started,
        "host_frontend_cpu_seconds": time.process_time() - cpu_started,
    }


def parsed_functions(text):
    module = verify_ir_text(text)
    functions = {}
    for function in module.functions:
        if function.name in ("user_flow_experiment_probe", "user_flow_experiment_void_probe"):
            blocks = get_indexed_function_kernel(function).materialize_legacy_blocks(function)
            functions[function.name] = blocks
    assert len(functions) == 2, "fixture must emit both object and void return functions"
    return module, functions


def _cleanup_sequence(block, addresses, *, strict):
    """Recognize actual load/unpin/clear instructions, not symbolic fake IR."""
    instructions = list(block.instructions)
    loads = [(index, ins) for index, ins in enumerate(instructions)
             if ins.kind == "load" and ins.data[0].startswith("gc.root.cleanup.unpin.")]
    sequence = []
    used_calls = set()
    for position, load in loads:
        value = load.data[0]
        # Parsed loads carry (destination, result type, pointer type, address).
        slot = addresses.slot(load.data[3])
        calls = [(index, ins) for index, ins in enumerate(instructions[position + 1:], position + 1)
                 if ins.kind == "call"]
        assert len(calls) >= 2, "pinned cleanup is missing unpin or root clear"
        unpin_index, unpin = calls[0]
        clear_index, clear = calls[1]
        assert addresses.called(unpin, "pcc_gc_unpin"), "pinned cleanup must unpin before clearing"
        assert addresses.args(unpin) == [value], "unpin must consume this slot's current load"
        assert addresses.called(clear, "pcc_gc_store_root"), "pinned cleanup is missing root clear"
        clear_slot, replacement = addresses.args(clear)
        assert addresses.slot(clear_slot) == slot and replacement == "null", "cleanup must clear the same physical slot"
        used_calls.update((unpin_index, clear_index))
        sequence.append(slot)
    if strict:
        assert len(loads) == 1, "a shared suffix node must contain one current slot load"
        actual_calls = {index for index, ins in enumerate(instructions) if ins.kind == "call"}
        assert actual_calls == used_calls, "shared suffix contains an unmodeled effect"
    return tuple(sequence)


def check_contract(module, functions, snapshots, *, inline=False, require_nonempty=True, check_frames=True):
    """Reuse root/frame analysis and add ONLY this bounded suffix contract.

    Snapshots are observations of real emitter selection, retained separately
    from parsed IR so a damaged generated artifact cannot redefine expectations.
    This is not a general ownership/lease proof for arbitrary PCC programs.
    """
    signatures = {}
    counts = Counter()
    for name, blocks in functions.items():
        addresses = RootSlotContract(blocks, module.globals_)
        if check_frames:
            addresses.assert_frame_exits()  # Existing checker, including LIFO joins.
        by_name = {block.name: block for block in blocks}
        physical_roots = [dest for dest in addresses.allocas if dest.startswith("literal.splat.list.tmp.root.")]
        ranks = {(dest, 0): index for index, dest in enumerate(physical_roots)}
        selected = [snapshot for snapshot in snapshots if snapshot.function == name]
        assert selected, "fixture needs observed production return paths"
        path_signatures = []
        suffix_names = set()
        for snapshot in selected:
            entry = by_name[snapshot.entry]
            assert entry.terminator.kind == "br", "observed cleanup predecessor must branch"
            if inline:
                sequence = _cleanup_sequence(entry, addresses, strict=False)
                assert entry.terminator.data[0] == snapshot.target
            else:
                assert entry.terminator.data[0] == snapshot.head, "return bypassed or changed its selected suffix"
                actual, seen = [], set()
                head = snapshot.head
                while head != snapshot.target:
                    assert head not in seen, "shared pinned suffix must terminate"
                    seen.add(head)
                    node = by_name[head]
                    assert node.name.startswith("ret.shared.unpin."), "unexpected cleanup path effect"
                    actual.extend(_cleanup_sequence(node, addresses, strict=True))
                    assert node.terminator.kind == "br", "suffix must have one successor"
                    head = node.terminator.data[0]
                suffix_names.update(seen)
                sequence = tuple(actual)
                expected = tuple(addresses.slot(slot) for slot in snapshot.slots)
                assert sequence == expected, "return cleanup differs from its selected physical slot sequence"
            leave = by_name[snapshot.target]
            assert leave.name.startswith("ret.shared.leave.") and leave.terminator.kind == "br"
            tail = by_name[leave.terminator.data[0]]
            assert tail.name.startswith("ret.shared.tail.")
            calls = [ins for ins in tail.instructions if ins.kind == "call"]
            if snapshot.rooted:
                assert len(calls) >= 3
                assert [ins.data[2] for ins in calls[:3]] == [
                    "pcc_gc_load_ptr", "pcc_gc_store_root", "pcc_gc_frame_leave_lifo",
                ], "rooted return must reload before clearing and leaving its LIFO root"
                root = addresses.slot(addresses.args(calls[0])[1])
                assert addresses.slot(addresses.args(calls[1])[0]) == root
                assert addresses.args(calls[1])[1] == "null"
                assert addresses.slot(addresses.args(calls[2])[0]) == root
                assert tail.terminator.kind == "ret" and tail.terminator.data[1] == calls[0].data[0]
            else:
                assert not any(addresses.called(ins, "pcc_gc_frame_leave_lifo") for ins in calls)
            assert all(slot in ranks for slot in sequence), "fixture cleanup escaped its actual persistent roots"
            path_signatures.append((snapshot.rooted, tuple(ranks[slot] for slot in sequence)))
        if require_nonempty:
            assert len(physical_roots) >= 1 and any(sequence for _, sequence in path_signatures)
            assert path_signatures[0][1] == (), "first return must precede every late root"
            if not inline:
                assert suffix_names, "shared suffix fixture must not pass vacuously"
        signatures[name] = tuple(path_signatures)
        counts["blocks"] += len(blocks)
        counts["suffix_blocks"] += len(suffix_names)
        counts["return_paths"] += len(selected)
        for block in blocks:
            counts["instructions"] += len(block.instructions) + len(block.phis) + 1
            for ins in block.instructions:
                if ins.kind == "call":
                    counts["static_calls"] += 1
                    counts["call:" + ins.data[2]] += 1
    counts["cleanup_path_unpins"] = sum(len(sequence) for values in signatures.values() for _, sequence in values)
    return signatures, dict(counts)


def validate(text, snapshots, *, inline=False):
    started = time.perf_counter()
    module, functions = parsed_functions(text)
    parsed = time.perf_counter()
    signatures, counts = check_contract(module, functions, snapshots, inline=inline)
    checked = time.perf_counter()
    check_contract(module, functions, snapshots, inline=inline, check_frames=False)
    suffix_checked = time.perf_counter()
    return signatures, counts, {
        "parse_verify_seconds": parsed - started,
        "root_and_suffix_contract_seconds": checked - parsed,
        "suffix_only_seconds": suffix_checked - checked,
    }


def mutate(text, snapshots, kind):
    """Damage generated text only; normal owned parse/verify still must pass."""
    chosen = next(s for s in snapshots if s.function == "user_flow_experiment_probe" and s.slots)
    pattern = re.compile(r"(?m)^" + re.escape(chosen.head) + r":\n(?P<body>.*?)(?=^\S[^\n]*:|^})", re.S)
    matches = list(pattern.finditer(text))
    assert len(matches) == 1, "mutation needs one exact generated suffix"
    match = matches[0]
    lines = match.group("body").splitlines(keepends=True)
    if kind in ("delete_unpin", "delete_clear", "clear_before_unpin"):
        unpin = next(i for i, line in enumerate(lines) if "@pcc_gc_unpin(" in line)
        clear = next(i for i, line in enumerate(lines) if "@pcc_gc_store_root(" in line)
        if kind == "delete_unpin":
            del lines[unpin]
        elif kind == "delete_clear":
            del lines[clear]
        else:
            lines[unpin], lines[clear] = lines[clear], lines[unpin]
        changed = text[:match.start("body")] + "".join(lines) + text[match.end("body"):]
    elif kind in ("bypass_suffix", "add_late_cleanup"):
        target = chosen.target
        if kind == "add_late_cleanup":
            later = next(s for s in snapshots if s.function == chosen.function and len(s.slots) > len(chosen.slots))
            target = later.head
        entry_pattern = re.compile(r"(?m)^" + re.escape(chosen.entry) + r":\n(?P<body>.*?)(?=^\S[^\n]*:|^})", re.S)
        entry = entry_pattern.search(text)
        assert entry is not None
        old = "br label %" + chosen.head
        body = entry.group("body")
        assert body.count(old) == 1
        changed = text[:entry.start("body")] + body.replace(old, "br label %" + target) + text[entry.end("body"):]
    else:
        raise ValueError(kind)
    assert changed != text, "negative mutation did not apply"
    return changed


NEGATIVE_CASES = ("delete_unpin", "delete_clear", "clear_before_unpin", "bypass_suffix", "add_late_cleanup")


def negative_controls(text, snapshots):
    result = {}
    for kind in NEGATIVE_CASES:
        damaged = mutate(text, snapshots, kind)
        module, functions = parsed_functions(damaged)
        # Each negative is structurally valid and preserves frame balance.
        for blocks in functions.values():
            RootSlotContract(blocks, module.globals_).assert_frame_exits()
        try:
            check_contract(module, functions, snapshots)
        except AssertionError as exc:
            result[kind] = str(exc)
        else:
            raise AssertionError("damaged real IR unexpectedly accepted: " + kind)
    return result


def experiment(*, roots=(1, 2, 4, 8), samples=5, source_revision):
    if not 1 <= samples <= 9:
        raise ValueError("use 1..9 bounded samples")
    rows = []
    for count in roots:
        source = source_program(count)
        records = {"shared": [], "inline": []}
        pair = {}
        frozen = {}
        for inline in (False, True):
            text, snapshots, _ = emit(source, inline=inline)
            label = "inline" if inline else "shared"
            frozen[label] = (text, snapshots)
            validate(text, snapshots, inline=inline)
        # Balanced warmup, then alternating order to reduce temporal bias.
        for inline in (False, True):
            text, _, _ = emit(source, inline=inline, observe=False)
            label = "inline" if inline else "shared"
            expected, snapshots = frozen[label]
            assert text == expected, "observation hooks changed the generated IR"
            validate(text, snapshots, inline=inline)
        for index in range(samples):
            for inline in ((False, True) if index % 2 == 0 else (True, False)):
                label = "inline" if inline else "shared"
                text, _, timings = emit(source, inline=inline, observe=False)
                expected, snapshots = frozen[label]
                assert text == expected, "timed emission drifted from validated IR"
                signature, static, checks = validate(text, snapshots, inline=inline)
                records[label].append(timings | checks)
                pair[label] = signature
                if label == "shared" and count == max(roots):
                    final_text, final_snapshots = text, snapshots
                records[label][-1]["static"] = static
            assert pair["shared"] == pair["inline"], "real inline and shared cleanup paths disagree"
        row = {"persistent_roots_per_function": count, "source_sha256": hashlib.sha256(source.encode()).hexdigest()}
        for label, measured in records.items():
            row[label] = {key: statistics.median(item[key] for item in measured)
                          for key in measured[0] if key != "static"}
            row[label]["static"] = measured[-1]["static"]
            row[label]["host_frontend_wall_range_seconds"] = [
                min(item["host_frontend_seconds"] for item in measured),
                max(item["host_frontend_seconds"] for item in measured),
            ]
        row["static_calls_saved"] = row["inline"]["static"]["static_calls"] - row["shared"]["static"]["static_calls"]
        row["host_frontend_ratio_shared_over_inline"] = row["shared"]["host_frontend_seconds"] / row["inline"]["host_frontend_seconds"]
        row["host_cpu_ratio_shared_over_inline"] = row["shared"]["host_frontend_cpu_seconds"] / row["inline"]["host_frontend_cpu_seconds"]
        rows.append(row)
    assert max(roots) >= 2, "late-root negative requires at least two actual persistent roots"
    negatives = negative_controls(final_text, final_snapshots)
    return {
        "boundary": "real PCC host frontend emission and owned-IR checks; NO native/GC execution",
        "source_revision": source_revision, "python": platform.python_version(),
        "platform": platform.platform(), "samples_per_variant": samples,
        "control": "production inline pinned cleanup; shared frame exits remain enabled",
        "rows": rows, "rejected_real_ir_mutations": negatives,
        "performance_limit": "static call savings do not imply fewer calls on an executed path or native speedup",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--max-roots", type=int, default=8, choices=(2, 4, 8, 16))
    args = parser.parse_args()
    roots = tuple(value for value in (1, 2, 4, 8, 16) if value <= args.max_roots)
    print(json.dumps(experiment(roots=roots, samples=args.samples, source_revision=args.source_revision), indent=2))


if __name__ == "__main__":
    main()
