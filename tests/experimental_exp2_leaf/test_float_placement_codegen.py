"""M11 placement changes only two native calls in one proven terminal route."""
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from tests.python.test_gc3_ceiling_experiment import function

FLAG = "PCC_EXPERIMENT_GC3_FLOAT_PLACEMENT"
OLD_BOX = "pcc_exp2_float_from_f64_old"
OLD_MUL = "pcc_exp2_obj_mul_slots_old"
CLIENT = """\
import placement_provider as native
from pcc.extern import extern, c_double
opaque = extern("unqualified_native_scalar", (), c_double)

def escapes():
    baseline = native.sample()
    yield 0
    return (native.sample() - baseline) * 17.75

def different_peer():
    yield 0
    return (native.sample() - 42.0) * 0.5

def crosses():
    baseline = native.sample()
    yield 0
    return (native.sample() - baseline) * (yield 1)

def nonterminal():
    baseline = native.sample()
    yield 0
    result = (native.sample() - baseline) * 17.75
    return result

def nested_return(callback):
    baseline = native.sample()
    yield 0
    return callback((native.sample() - baseline) * 17.75)

def conditional_result(flag):
    baseline = native.sample()
    yield 0
    return ((native.sample() - baseline) * 17.75 if flag else baseline)

def pending_finally(sink):
    baseline = native.sample()
    yield 0
    try:
        return (native.sample() - baseline) * 17.75
    finally:
        sink()

def unsafe_peer(unknown):
    baseline = native.sample()
    yield 0
    return (native.sample() - baseline) * unknown

def direct_scalar_result():
    yield 0
    return native.sample()

def borrowed_sub_peer(other):
    yield 0
    return (native.sample() - other) * 17.75

def callback_producer(cb):
    baseline = native.sample()
    yield 0
    return (cb() - baseline) * 17.75

def opaque_producer():
    baseline = native.sample()
    yield 0
    return (opaque() - baseline) * 17.75

def no_terminal_reduce():
    yield 0
    return native.sample() * 17.75
"""


def compile_fixture(root, enabled, ceiling="0"):
    from pcc.frontends.python.pipeline import compile_python_multi
    provider = root / "placement_provider.py"
    provider.write_text("def sample() -> float:\n    return 31.5\n")
    client = root / "placement_client.py"
    client.write_text(CLIENT)
    out = root / ("placement_" + enabled + ".ll")
    env = {FLAG: enabled, "PCC_EXPERIMENT_GC3_CEILING": ceiling,
           "PCC_EXPERIMENT_GC3_LEXICAL": "1", "PCC_EXPERIMENT_GC3_LEASE_SITES": "1",
           "PCC_EXPERIMENT_GC3_MAPPED_FLOAT": "1"}
    with patch.dict(os.environ, env):
        compile_python_multi(
            [str(client), str(provider)], str(out), entry_module="placement_client",
            backend="self", target_triple="x86_64-unknown-linux-gnu",
            ir_scaffold_mode="on", emit_llvm_only=True,
        )
    return out.read_text()


def fn(ir, name):
    return function(ir, "user_placement_client_" + name + "__gen_resume")


class FloatPlacementCodegen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="m11-placement-") as temp:
            cls.before = compile_fixture(Path(temp), "0")
            cls.after = compile_fixture(Path(temp), "1")

    def test_combined_actual_terminal_sites_not_beginning_provider(self):
        self.assertNotIn("@" + OLD_BOX, self.before)
        self.assertNotIn("@" + OLD_MUL, self.before)
        for name in ("escapes", "different_peer"):
            body = fn(self.after, name)
            self.assertEqual(body.count("@" + OLD_BOX + "("), 1, name)
            self.assertEqual(body.count("@" + OLD_MUL + "("), 1, name)
        self.assertIn("@py_float_from_f64", fn(self.after, "escapes"))
        self.assertIn("@py_obj_call_slots", fn(self.after, "escapes"))

    def test_opaque_suspended_nonterminal_uncertain_peers_stay_old_policy(self):
        for name in ("crosses", "nonterminal", "nested_return", "conditional_result",
                     "pending_finally", "unsafe_peer", "direct_scalar_result", "borrowed_sub_peer",
                     "callback_producer", "opaque_producer", "no_terminal_reduce"):
            with self.subTest(name=name):
                body = fn(self.after, name)
                self.assertNotIn("@" + OLD_BOX, body)
                self.assertNotIn("@" + OLD_MUL, body)
                for sym in ("py_float_from_f64", "py_obj_mul_slots", "pcc_gc_foreign_lease_release",
                            "pcc_exp2_foreign_lease_acquire_site"):
                    self.assertEqual(body.count("@" + sym + "("), fn(self.before, name).count("@" + sym + "("))

    def test_scalar_preserves_original_site1_and_mul_preserves_true_escape(self):
        body = fn(self.after, "escapes")
        fv = re.search(r"(%m\.flt_box[^ ]*) = call ptr \(double\) @" + OLD_BOX
                       + r"\(double %[^)]+\)\n  store ptr \1, ptr (%[^\n]+)", body)
        self.assertIsNotNone(fv)
        slot = re.escape(fv[2])
        lease = re.search(r"(%call\.slot\.publish\.lease[^ ]*) = call [^\n]*"
                          r"@pcc_exp2_foreign_lease_acquire_site\(ptr " + slot + r", i64 1\)", body)
        self.assertIsNotNone(lease)
        self.assertRegex(body, r"@pcc_gc_foreign_lease_release\(ptr " + slot + r", i64 " + re.escape(lease[1]) + r"\)")
        self.assertRegex(body, r"@pcc_gc_note_slot_write_barrier\(ptr null, ptr " + slot + r", ptr ")
        self.assertEqual(body.count(", i64 3)"), fn(self.before, "escapes").count(", i64 3)"))

        mul = re.search(r"(%[^ ]+) = call i64 \(ptr, ptr, ptr\) @" + OLD_MUL
                        + r"\((ptr %[^,]+, ptr %[^,]+, ptr (%[^)]+))\)", body)
        self.assertIsNotNone(mul)
        self.assertRegex(body, r"@py_obj_mul_slots\(" + re.escape(mul[2]) + r"\)")
        self.assertRegex(body, r"= phi i64 \[" + re.escape(mul[1]) + r", %[^]]+\], \[%[^]]+\]")
        self.assertRegex(body, r"@pcc_gc_pin_slot_for_take\(ptr " + re.escape(mul[3]) + r", i64 0\)")
        self.assertIn("@py_gen_finish", body)
