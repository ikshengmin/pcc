"""Scratch-only GC3 codegen experiment: never infer the runtime choice from build env."""
import re
import unittest


def generated(src, name="gc3_example", enabled=True):
    import os
    from unittest.mock import patch
    from pcc.frontends.python import type_infer
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    module = type_infer.infer_module(parse_and_lift(src, name + ".py", name))
    host = L1CodeGen(module, ir_scaffold_mode="on")
    host._strict_no_libpython = True
    with patch.dict(os.environ, {"PCC_EXPERIMENT_GC3_CEILING": "1" if enabled else "0"}):
        return str(host.generate(module))


def function(text, symbol):
    start = re.search(r"(?m)^define [^\n]*?@" + re.escape(symbol) + r"\([^\n]*\{\n", text)
    assert start, symbol
    return text[start.end():text.index("\n}", start.end())]


SOURCE = "def alias(value):\n    left = [value]\n    right = [left, value]\n    return right\n"
BASE_SYMBOL = "user_gc3_example_alias"


class GC3CeilingTest(unittest.TestCase):
    def test_guard_and_managed_body_preserve_roots_barriers_independent_control(self):
        base = generated(SOURCE, enabled=False)
        cand = generated(SOURCE)
        before = function(base, BASE_SYMBOL)
        after = function(cand, BASE_SYMBOL)
        self.assertNotIn("exp.gc3.", before)
        self.assertIn(before, after)  # byte-identical fallback body, not just equivalent output
        self.assertLess(after.index("@pcc_gc_backend_frozen"), after.index("@pcc_gc_backend_selected"))
        self.assertIn("@pcc_gc_refcount_provenance_probe", after)
        self.assertIn("@pcc_diagnostics_runtime_log_fast_state", after)
        fast = after.split("exp.gc3.fast."+before.split(":")[0]+":", 1)[1]
        calls = lambda b, s: len(re.findall(r"call [^\n@]*@" + s + r"\(", b))
        for old in ("pcc_gc_release", "pcc_gc_retain", "pcc_gc_store_root"):
            self.assertGreater(calls(before, old), 0)
            self.assertEqual(calls(fast, old), 0)
        for kept in ("pcc_gc_load_ptr", "pcc_gc_pin", "pcc_gc_unpin", "pcc_gc_safepoint", "pcc_thread_safepoint_gate", "py_recursion_enter"):
            self.assertEqual(calls(before, kept), calls(fast, kept), kept)
        self.assertEqual(calls(before,"pcc_gc_store_root"), calls(fast,"pcc_exp_gc3_root_gc3_example"))
        root = function(cand, "pcc_exp_gc3_root_gc3_example")
        self.assertLess(root.index("store ptr %value"), root.index("@pcc_gc_mark_active"))
        self.assertLess(root.index("@pcc_gc_mark_active"), root.index("@pcc_gc_count_free_barrier_shade"))
        self.assertIn("call void () @pcc_gc_note_store()", root)
        for symbol in ("main", BASE_SYMBOL+"_native_adapter"):
            self.assertEqual(function(cand,symbol),function(base,symbol))

    def test_unsupported_owner_is_exact_original(self):
        for prefix in ("__pcc_runtime_port__ = True\n", "from pcc import i64\n"):
            self.assertEqual(generated(prefix+SOURCE), generated(prefix+SOURCE, enabled=False))

    def test_leaf_rewrites_fail_closed(self):
        from pcc.frontends.python.codegen.gc3_ceiling_experiment import rewrite_instruction
        rewrite = lambda line: rewrite_instruction("  "+line, "root", set(), set())
        keep = (
           "call void (ptr) @pcc_gc_pin(ptr %address)",
           "call void (ptr) @pcc_gc_unpin(ptr %address)",
           "%n = call i64 (ptr) @pcc_gc_foreign_lease_acquire(ptr %slot)",
           "%n = call i64 (ptr, i64) @pcc_gc_foreign_lease_release(ptr %slot, i64 %token)",
           "%n = call i64 (ptr, ptr) @pcc_gc_root_copy_lease(ptr %dst, ptr %src)",
           "call void (ptr) @Py_INCREF(ptr %handle)",
           "call void (ptr) @Py_DECREF(ptr %handle)",
           "call void (ptr, ptr, ptr) @pcc_gc_store_ptr(ptr %owner, ptr %slot, ptr %value)",
           "call void () @pcc_gc_safepoint()",
        )
        for line in keep:
            self.assertEqual(rewrite(line), "  "+line)
        for line in ("call void (ptr) @pcc_gc_pin(ptr null)",
                     "call void (ptr) @pcc_gc_unpin(ptr @py_true_storage)"):
            self.assertEqual(rewrite(line), "")
        self.assertIn("add i64 0, 0", rewrite("%n = call i64 (ptr, i64) @pcc_gc_foreign_lease_release(ptr %slot, i64 0)"))
        # A simple pointer parameter can be a heap object even if its name sounds scalar.
        self.assertEqual(rewrite("call void (ptr) @pcc_gc_pin(ptr %tagged)"),
                         "  call void (ptr) @pcc_gc_pin(ptr %tagged)")


if __name__ == "__main__":
    unittest.main()
