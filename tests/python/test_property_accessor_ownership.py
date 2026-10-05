"""Property assembly retains accessor owners across later metadata work."""
from __future__ import annotations

from collections import deque
import re

import pytest

from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module


def _emit(source):
    module = infer_module(parse_and_lift(source, "property_owner.py", "property_owner"))
    codegen = L1CodeGen(module, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    return str(codegen.generate(module))


def _source(accessors, failing_default=False):
    source = "def make_default():\n    return ['owned']\n"
    if failing_default:
        source += "def later_default():\n    raise ValueError('later accessor')\n"
    source += (
        "class Holder:\n"
        "    @property\n"
        "    def value(self, token=make_default()):\n"
        "        return token\n"
    )
    if "setter" in accessors:
        default = "later_default()" if failing_default else "make_default()"
        source += (
            "    @value.setter\n"
            "    def value(self, new_value, token=" + default + "):\n"
            "        pass\n"
        )
    if "deleter" in accessors:
        source += (
            "    @value.deleter\n"
            "    def value(self, token=make_default()):\n"
            "        pass\n"
        )
    return source


def _property_bodies(text):
    bodies = re.findall(r"^define [^\n]*\{\n.*?^}", text, re.M | re.S)
    selected = [body for body in bodies if re.search(r"\bcall [^\n]*@py_property_new\(", body)]
    headers = [body.splitlines()[0] for body in selected]
    assert len(headers) == 2, headers
    assert any("@_pcc_py_module_init_property_owner(" in line for line in headers), headers
    assert any("@main(" in line for line in headers), headers
    return selected


def _owners_and_cfg(body, expected):
    aliases = dict(re.findall(r"(%[-\w.]+) = bitcast ptr (%[-\w.]+) to ptr", body))

    def original(value):
        seen = set()
        while value in aliases:
            assert value not in seen
            seen.add(value)
            value = aliases[value]
        return value

    roots = {}
    for role in ("descriptor", "getter", "setter", "deleter"):
        matches = re.findall(r"(%class\.property\." + role + r"\.operand[-\w.]*) = alloca ptr", body)
        assert len(matches) == len(expected), (role, matches)
        roots[role] = matches
    all_roots = [root for matches in roots.values() for root in matches]
    bits = {root: 1 << index for index, root in enumerate(all_roots)}
    loads = dict(re.findall(r"(%[-\w.]+) = load ptr, ptr (%[-\w.]+)", body))

    # A terminal callable handoff must immediately publish to its preregistered
    # caller root, before another accessor may allocate signature/metadata.
    for stage, role in enumerate(expected):
        pattern = (r"(%[-\w.]+) = call [^\n]*@pcc_gc_take_pinned_slot\([^\n]*\)\n"
                   r"  store ptr \1, ptr " + re.escape(roots[role][stage]) + r"(?:,|\n)")
        assert re.search(pattern, body), role
    descriptors = list(re.finditer(r"(%[-\w.]+) = call [^\n]*@py_property_new\([^\n]*\)\n", body))
    assert len(descriptors) == len(expected)
    for stage, descriptor in enumerate(descriptors):
        following = body[descriptor.end():].splitlines()[0].strip()
        assert following.startswith("store ptr " + descriptor.group(1) + ", ptr " + roots["descriptor"][stage])

    # Track only these four physical roots, resolving address-preserving casts.
    # Every reachable constructor call must see all produced accessors live;
    # every normal/error return must have retired all construction owners.
    blocks = {}
    current = None
    for line in body.splitlines()[1:-1]:
        label = re.match(r"^([-\w.$]+):", line)
        if label:
            current = label.group(1)
            blocks[current] = []
        elif current is not None:
            blocks[current].append(line.strip())
    assert blocks
    queue = deque([(next(iter(blocks)), 0)])
    seen = set()
    constructor_states = []
    return_states = []
    while queue:
        block, state = queue.popleft()
        if (block, state) in seen:
            continue
        seen.add((block, state))
        assert block in blocks, block
        successors = []
        for line in blocks[block]:
            if "call " in line and "@py_property_new(" in line:
                constructor_states.append(state)
                arguments = re.findall(r"ptr (%[-\w.]+)", line)
                argument_roots = [original(loads[value]) for value in arguments]
                assert len(argument_roots) == 3
                stage = roots["getter"].index(argument_roots[0])
                assert argument_roots == [roots[role][stage] for role in ("getter", "setter", "deleter")]
                produced = ("getter",) if stage == 0 else ("getter", "setter", "deleter")
                expected_mask = sum(bits[roots[role][stage]] for role in produced)
                expected_mask |= sum(bits[root] for root in roots["descriptor"][:stage])
                assert state == expected_mask, (block, state, expected_mask)
            store = re.search(r"store ptr (null|%[-\w.]+), ptr (%[-\w.]+)", line)
            if store:
                root = original(store.group(2))
                if root in bits:
                    if store.group(1) == "null":
                        state &= ~bits[root]
                    else:
                        state |= bits[root]
            clear = re.search(r"@pcc_gc_store_root\(ptr (%[-\w.]+), ptr null\)", line)
            if clear:
                root = original(clear.group(1))
                if root in bits:
                    state &= ~bits[root]
            if line.startswith("br "):
                successors.extend(re.findall(r"label %([-\w.$]+)", line))
            if line.startswith("ret "):
                return_states.append(state)
                assert state == 0, (block, state)
        queue.extend((successor, state) for successor in successors)
    assert constructor_states and return_states
    for stage, role in enumerate(expected):
        # Resolve lease operands through the same physical alias map.
        leases = re.findall(r"@pcc_gc_foreign_lease_acquire\(ptr (%[-\w.]+)\)", body)
        for root in (roots[role][stage], roots["descriptor"][stage]):
            assert any(original(slot) == root for slot in leases), (role, stage)
    assert "property namespace publication" in body or "call.slot.report" in body


@pytest.mark.parametrize("accessors,failing_default", [
    (("getter",), False),
    (("getter", "setter"), False),
    (("getter", "setter", "deleter"), False),
    (("getter", "setter"), True),
])
def test_property_assembly_preserves_accessor_roots_and_cleanup(accessors, failing_default):
    for body in _property_bodies(_emit(_source(accessors, failing_default))):
        _owners_and_cfg(body, accessors)


@pytest.mark.parametrize('name', [
    'test_property_aliases_preserve_lexical_replacements',
    'test_property_later_accessor_default_failure_preserves_exception',
])
def test_interleaved_property_alias_frames_and_defaults_compile(name):
    import ast
    from pathlib import Path
    import textwrap
    from class_method_ir_checks import check_method_frames

    tree = ast.parse(Path(__file__).with_name('test_descriptor_protocol.py').read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    source = next(node.args[1].value for node in ast.walk(function)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id == '_compile_and_run')
    source = textwrap.dedent(source)
    text = _emit(source)
    assert check_method_frames(text)
    bodies = _property_bodies(text)
    for body in bodies:
        assert '@py_obj_getattr(' in body
        # The lexical descriptor exists before reading its accessor. There is
        # no raw-method-table fallback in the property alias producer.
        assert body.index('@py_property_new(') < body.index('@py_obj_getattr(')
