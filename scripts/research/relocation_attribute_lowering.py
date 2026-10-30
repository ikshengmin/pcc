#!/usr/bin/env python3
"""Bounded, host-only check of a rejected same-module relocation counter.

Use actual PCC sources and its export/inference/emission/verification APIs.
Only the proposed helper is added in memory; no production source is edited.
This deliberately does not reproduce the complete Stage1 export graph.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import re
import sys


MODULES = (
    "pcc.backend.native_object", "pcc.backend.macho_obj",
    "pcc.backend.macho_spec", "pcc.backend.self_backend_value_arena",
)
PROPOSAL_NAME = "_research_count_relocation_rows"
PROPOSAL = '''
def _research_count_relocation_rows(relocations) -> int:
    count = len(relocations)
    for entry in relocations:
        if type(entry) is Relocation:
            relocation: Relocation = cast(Relocation, entry)
            if relocation.addend:
                count += 1
            if relocation.type == spec.ARM64_RELOC_SUBTRACTOR:
                count += 1
        else:
            if entry.addend:
                count += 1
            if entry.type == spec.ARM64_RELOC_SUBTRACTOR:
                count += 1
    return count
'''


def function_text(text, name):
    matches = re.findall(
        r"^define[^\n]*@" + re.escape(name) + r"\([^\n]*\) \{\n.*?^\}",
        text, re.M | re.S,
    )
    if len(matches) != 1:
        raise ValueError("expected exactly one emitted function: " + name)
    body = matches[0]
    if "strict.nolib.stub:" in body or "@py_cpy_" in body:
        raise ValueError("selected function is unavailable without libpython: " + name)
    return body


def attribute_calls(text):
    return dict(Counter(
        match.group(1) for line in text.splitlines()
        if "call " in line and "@py_obj_getattr(" in line
        for match in [re.search(r"@\.pyattr\.([A-Za-z_][A-Za-z_0-9]*)", line)]
        if match is not None
    ))


def exact_branch_text(body):
    """Separate the proposal's exact-type arm from its fallback in real CFG.

    Stop at the shared join and all re-entry edges. This is a narrow witness
    for this one proposal, not a general optimizer or synthetic IR checker.
    Fail closed if the emitted guard/join shape changes.
    """
    blocks = {}
    current = None
    for line in body.splitlines()[1:]:
        if re.fullmatch(r"[A-Za-z_.$][A-Za-z_0-9.$-]*:", line):
            current = line[:-1]
            if current in blocks:
                raise ValueError("duplicate emitted block")
            blocks[current] = []
        elif current is not None:
            blocks[current].append(line)
    guards = []
    for name, lines in blocks.items():
        if any("load ptr, ptr @.class.pcc_backend_macho_obj.Relocation" in line for line in lines):
            branches = [re.fullmatch(
                r"\s*br i1 %is\.[^,]+, label %([^,]+), label %(\S+)", line,
            ) for line in lines]
            guards.extend((name, m.group(1), m.group(2)) for m in branches if m)
    if len(guards) != 1:
        raise ValueError("expected one actual Relocation identity guard")
    guard, exact, fallback = guards[0]
    suffix = exact.removeprefix("if.then.")
    if exact == suffix or not suffix.isdigit():
        raise ValueError("unrecognized exact-branch label")
    join = "if.end." + str(int(suffix) + 2)
    if join not in blocks or fallback not in blocks:
        raise ValueError("missing exact-branch join/fallback")
    pending, seen = [exact], set()
    while pending:
        name = pending.pop()
        if name in seen or name in (guard, fallback, join):
            continue
        if name not in blocks:
            raise ValueError("branch targets missing block")
        seen.add(name)
        for line in blocks[name]:
            if line.lstrip().startswith("br "):
                pending.extend(re.findall(r"label %([A-Za-z_0-9.$-]+)", line))
    return "\n".join(line for name in blocks if name in seen for line in blocks[name])


def run(repo, *, ir_output=None):
    repo = Path(repo).resolve()
    sys.path.insert(0, str(repo))
    from pcc.frontends.python import py_ast
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.type_infer import infer_module
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.pipeline_context import build_closed_world_context
    from pcc.frontends.python.pipeline_closed_world import (
        _closed_world_boxed_int_functions, _closed_world_function_object_exports,
    )
    from pcc.backend.self_backend_parse import parse_self_backend_module
    from pcc.backend.self_backend_verify import verify_parsed_module
    import pcc
    if not Path(pcc.__file__).resolve().is_relative_to(repo):
        raise ValueError("PCC imported from a different source tree")

    paths = [repo.joinpath(*name.split(".")).with_suffix(".py") for name in MODULES]
    parsed, exports, derived = build_closed_world_context([str(p) for p in paths], MODULES)
    cases = (
        ("baseline", MODULES[0], "_final_section_rows", False),
        ("proposed", MODULES[1], PROPOSAL_NAME, True),
        ("existing_append", MODULES[1], "_append_final_link_relocation_fields", False),
    )
    results = {}
    for label, owner, function, proposed in cases:
        index = MODULES.index(owner)
        module = parsed[index]
        if proposed:
            source = paths[index].read_text()
            if PROPOSAL_NAME in source:
                raise ValueError("proposal name already exists in source")
            module = parse_and_lift(source + "\n" + PROPOSAL, str(paths[index]), owner)
        external = {name: values for name, values in exports.items() if name != owner}
        typed = infer_module(module, external_exports=external, derived_class_map=derived)
        selected = replace(typed, body=tuple(node for node in typed.body if (
            isinstance(node, (py_ast.Import, py_ast.ImportFrom))
            or isinstance(node, py_ast.FuncDef) and node.name in (function, "_align_up")
            or isinstance(node, py_ast.ClassDef) and node.name == "Relocation"
        )))
        generator = L1CodeGen(selected, ir_scaffold_mode="on")
        generator._strict_no_libpython = True
        generator._prefer_native_callable_values = True
        generator._module_source_path = str(paths[index])
        generator._skip_program_main = True
        generator._sibling_module_inits = MODULES
        generator._native_module_exports = external
        generator._native_function_object_exports = _closed_world_function_object_exports(exports, owner)
        generator._native_boxed_int_functions = _closed_world_boxed_int_functions(exports, owner)
        # Match the isolated worker's fresh-instance entry; only retain text
        # here so the owned verifier and the bounded call witness can inspect it.
        generated = str(generator._generate_initial_module())
        body = function_text(generated, "user_" + owner.replace(".", "_") + "_" + function)
        verify_parsed_module(parse_self_backend_module(generated))
        counts = Counter(re.findall(r"call [^@\n]*@\"?([^\" (]+)", body))
        flags = {key: bool(getattr(generator, key, False)) for key in (
            "_strict_no_libpython", "_prefer_native_callable_values",
            "_runtime_port_module", "_freestanding_module", "_module_uses_raw_int_scaffold",
        )}
        results[label] = {
            "function": owner + "." + function, "attribute_sites": attribute_calls(body),
            "py_type_builtin_sites": counts["py_type_builtin"],
            "py_instance_get_field_sites": counts["py_instance_get_field"],
            "flags": flags, "owned_ir_verified": True,
        }
        if proposed:
            results[label]["exact_arm_attribute_sites"] = attribute_calls(exact_branch_text(body))
        if ir_output is not None:
            target = Path(ir_output)
            target.mkdir(parents=True, exist_ok=True)
            (target / (label + ".ll")).write_text(generated)
    return {
        "scope": "Host local lowering with four real-module exports, not full Stage1/native execution",
        "modules": list(MODULES), "cases": results,
        "interpretation": "Static call sites, not dynamic counts or a prepare-time speedup",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--ir-output", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.repo, ir_output=args.ir_output), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
