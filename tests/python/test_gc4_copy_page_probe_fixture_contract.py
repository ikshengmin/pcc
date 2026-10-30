"""Source-only checks of the inactive-mark native fixture's link boundary.

Do not import the native test or compiler: these checks read Python AST and C
text only. They do not establish native linkage or active-mark correctness.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/python/fixtures/gc4_copy_page_probe_native.c"
NATIVE_TEST = ROOT / "tests/python/test_gc4_copy_page_probe_native.py"
CONTRACT = ROOT / "tests/python/test_freestanding_gc_relocation_copy.py"
COPY = ROOT / "pcc/runtime/py/freestanding_gc_relocation_copy.py"
HELPERS = {
    "pcc_gc_gray_count_increment_acq_rel": ("()", "void", ""),
    "pcc_gc_gray_count_decrement_acq_rel": ("()", "void", ""),
    "pcc_gc_set_mark_color": (
        "(c_ptr, c_int64)", "void *obj, int64_t color", "(void)obj; (void)color; "
    ),
}


def _shape(source):
    return ast.dump(ast.parse(source, mode="eval").body, include_attributes=False)


def _c_body(source, signature):
    match = re.search(re.escape(signature) + r"\s*\{([^{}]*)\}", source)
    assert match is not None, signature
    return " ".join(match[1].split())


def _literal(node, values):
    """Read the snapshot literals without executing/importing their module."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, ast.Subscript):
        mapping = _literal(node.value, values)
        key = _literal(node.slice, values)
        assert type(mapping) is dict and type(key) is str
        return mapping[key]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_literal(node.operand, values)
    if isinstance(node, ast.List):
        return [_literal(item, values) for item in node.elts]
    if isinstance(node, ast.Dict):
        result = {}
        for key, value in zip(node.keys, node.values):
            if key is None:
                result.update(_literal(value, values))
            else:
                result[_literal(key, values)] = _literal(value, values)
        return result
    # The scale snapshot has two bounded list(range(...)) values.
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "list" and len(node.args) == 1 and not node.keywords):
        inner = node.args[0]
        assert (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                and inner.func.id == "range" and not inner.keywords)
        limits = [_literal(arg, values) for arg in inner.args]
        assert all(type(limit) is int and 0 <= limit <= 1000 for limit in limits)
        return list(range(*limits))
    raise AssertionError("unsupported snapshot literal: " + ast.dump(node))


def _copy_cases():
    values = {}
    names = {"SUCCESS", "PAGE_KEPT", "UNCHANGED", "CASES", "UNKNOWN_MEMBERSHIP"}
    for node in ast.parse(NATIVE_TEST.read_text()).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in names):
            values[node.targets[0].id] = _literal(node.value, values)
        elif (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and isinstance(node.value.func.value, ast.Name)
                and node.value.func.value.id == "CASES"):
            assert node.value.func.attr == "update"
            assert len(node.value.args) == 1 and not node.value.keywords
            values["CASES"].update(_literal(node.value.args[0], values))
    return values["CASES"]


def _helper_guards(node, guards=()):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in HELPERS:
            yield node.func.id, guards
    if isinstance(node, ast.If):
        test = ast.dump(node.test, include_attributes=False)
        yield from _helper_guards(node.test, guards)
        for child in node.body:
            yield from _helper_guards(child, guards + ((test, True),))
        for child in node.orelse:
            yield from _helper_guards(child, guards + ((test, False),))
    else:
        for child in ast.iter_child_nodes(node):
            yield from _helper_guards(child, guards)


class CopyFixtureSourceContract(unittest.TestCase):
    def test_missing_helpers_have_exact_raw_abi_and_unconditional_failure(self):
        fixture = FIXTURE.read_text()
        declarations = {
            node.targets[0].id: node.value
            for node in ast.parse(COPY.read_text()).body
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        }
        contract = ast.parse(CONTRACT.read_text())
        imports = next(ast.literal_eval(node.value) for node in contract.body
                       if isinstance(node, ast.Assign)
                       and isinstance(node.targets[0], ast.Name)
                       and node.targets[0].id == "RAW_FUNCTION_IMPORTS")
        self.assertTrue(HELPERS.keys() <= imports)
        for name, (signature, c_args, ignored) in HELPERS.items():
            self.assertEqual(ast.dump(declarations[name], include_attributes=False),
                             _shape(f'extern("{name}", {signature}, c_void)'))
            self.assertEqual(_c_body(fixture, f"void {name}({c_args})"),
                             ignored + f'unexpected_mark_helper("{name}");')
        self.assertEqual(
            _c_body(fixture, "static _Noreturn void unexpected_mark_helper(const char *symbol)"),
            'fprintf(stderr, "mark-inactive copy fixture unexpectedly called %s\\n", symbol); abort();',
        )
        self.assertIn("#include <stdlib.h>", fixture)
        self.assertIn("#include <stdio.h>", fixture)

    def test_all_original_copy_cases_keep_marking_and_header_colors_inactive(self):
        fixture = FIXTURE.read_text()
        self.assertEqual(fixture.count("pcc_gc_mark_active"), 1)
        self.assertIn("pcc_gc_mark_active = 0;", fixture)
        self.assertLess(fixture.index("pcc_gc_mark_active = 0;"),
                        fixture.index("void *result = pcc_gc_backend4_relocate_copy_preallocated_unlocked("))
        source_flags = int(re.search(r"#define SRC_FLAGS (\d+)u\b", fixture)[1])
        header_writes = re.findall(r"\bw32\(([^,]+),\s*12,\s*([^;]+)\);", fixture)
        self.assertEqual(header_writes, [
            ("&objects[0]", "2048"), ("&objects[0]", "0"),
            ("&objects[i]", "i == 0 ? SRC_FLAGS : 0"),
            ("&objects[1]", '!strcmp(mode, "multi-candidate") ? 2048 : 0'),
            ("&objects[0]", "SRC_FLAGS | 524288"), ("&target", "64 | 65536"),
        ])
        # Enumerate every branch of the verified header-write expressions.
        for flags in (0, 2048, source_flags, source_flags | 524288, 64 | 65536):
            self.assertEqual(flags & 56, 0)
        cases = _copy_cases()
        self.assertEqual(len(cases), 40)
        for name, expected in cases.items():
            with self.subTest(case=name):
                self.assertEqual(expected["source_flags"] & 56, 0)
                self.assertEqual(expected["target_flags"] & 56, 0)

    def test_runtime_helper_calls_are_unreachable_under_the_fixture_boundary(self):
        source = ast.parse(COPY.read_text())
        transaction = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                           and node.name == "pcc_gc_backend4_relocate_copy_preallocated_unlocked")
        preparation_start = next(i for i, node in enumerate(transaction.body)
                                 if isinstance(node, ast.AnnAssign)
                                 and isinstance(node.target, ast.Name)
                                 and node.target.id == "prior_to_flags")
        preparation_end = next(i for i, node in enumerate(transaction.body)
                               if isinstance(node, ast.If)
                               and any(name == "pcc_gc_gray_count_decrement_acq_rel"
                                       for name, _ in _helper_guards(node)))
        preparation = ast.Module(body=transaction.body[preparation_start:preparation_end],
                                 type_ignores=[])
        expected_preparation = ast.parse('''
prior_to_flags: i64 = load_i32(to_obj, 12)
to_residency: i64 = prior_to_flags & 331776
memmove(to_obj, from_obj, size)
store_i64(to_obj, 0, 1)
new_flags: i64 = load_i32(to_obj, 12)
if load_i32(global_addr("pcc_gc_mark_active"), 0) != 0:
    new_flags = (new_flags & ~56) | (prior_to_flags & 56)
store_i32(to_obj, 12, (new_flags & ~343040) | to_residency)
''')
        self.assertEqual(ast.dump(preparation, include_attributes=False),
                         ast.dump(expected_preparation, include_attributes=False))
        decrement = _shape("(prior_to_flags & 16) != 0 and (new_flags & 16) == 0")
        increment = _shape("(prior_to_flags & 16) == 0 and (new_flags & 16) != 0")
        active = _shape('load_i32(global_addr("pcc_gc_mark_active"), 0) != 0')
        target_not_gray = _shape("(load_i32(to_obj, 12) & 16) == 0")
        self.assertEqual(list(_helper_guards(transaction)), [
            ("pcc_gc_gray_count_decrement_acq_rel", ((decrement, True),)),
            ("pcc_gc_gray_count_increment_acq_rel", ((decrement, False), (increment, True))),
            ("pcc_gc_gray_count_increment_acq_rel", ((active, True), (target_not_gray, True))),
            ("pcc_gc_set_mark_color", ((active, True),)),
        ])
        # The first two guards are false when both copied and prior target
        # flags have no GRAY bit. The final two require active marking, which
        # the fixture explicitly disables. This is static reachability only.
        prior_to_flags = 64 | 65536
        source_flags = int(re.search(r"#define SRC_FLAGS (\d+)u\b", FIXTURE.read_text())[1])
        for new_flags in (source_flags, source_flags | 524288):
            self.assertFalse((prior_to_flags & 16) != 0 and (new_flags & 16) == 0)
            self.assertFalse((prior_to_flags & 16) == 0 and (new_flags & 16) != 0)


if __name__ == "__main__":
    unittest.main()
