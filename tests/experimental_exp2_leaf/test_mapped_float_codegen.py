"""M10: finite private-use publication proof; check the actual generated Linux IR."""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.python.test_gc3_ceiling_experiment import function

FLAG = "PCC_EXPERIMENT_GC3_MAPPED_FLOAT"
HELPER = "pcc_exp2_publish_mapped_float"
TAGGED = "pcc_exp2_foreign_lease_acquire_site"
RELEASE = "pcc_gc_foreign_lease_release"
CLIENT = """\
import local_native_clock as timer
from pcc.extern import extern, c_double
raw = extern("opaque_scalar_provider", (), c_double)

def literal_peer():
    yield 0
    return (timer.tick() - 2.25) * 123.0

def actual_frame_local():
    started = timer.tick()
    yield 0
    return (timer.tick() - started) * 321.0

def other_consumer():
    started = timer.tick()
    yield 0
    return (timer.tick() - started) + 87.0

def across_yield():
    started = timer.tick()
    yield 0
    return (timer.tick() - started) * (yield 1)

def named_across_yield():
    started = timer.tick()
    intermediate = timer.tick() - started
    yield 0
    return intermediate * 3.0

def final_value():
    yield 0
    return timer.tick()

def final_sub(value):
    yield 0
    return timer.tick() - value

def raw_producer():
    yield 0
    return (raw() - 2.25) * 3.0

def generic_user_callback(callback):
    yield 0
    return (callback() - 2.25) * 3.0

def callback_consumer(callback):
    started = timer.tick()
    yield 0
    return callback(timer.tick() - started)

def borrowed_parameter(value):
    yield 0
    return (timer.tick() - value) * 3.0

def conditional(flag):
    started = timer.tick()
    yield 0
    return ((timer.tick() if flag else timer.tick()) - started) * 3.0
"""


def build(flag):
    from pcc.frontends.python.pipeline import compile_python_multi
    with tempfile.TemporaryDirectory(prefix="m10-mapped-float-") as t:
        d = Path(t)
        provider = d / "local_native_clock.py"
        provider.write_text("def tick() -> float:\n    return 7.5\n")
        client = d / "mapped_client.py"
        client.write_text(CLIENT)
        out = d / "native.ll"
        with patch.dict(os.environ, {
            FLAG: flag,
            "PCC_EXPERIMENT_GC3_LEASE_SITES": "1",
            "PCC_EXPERIMENT_GC3_LEXICAL": "1",
            "PCC_EXPERIMENT_GC3_CEILING": "0",
        }):
            compile_python_multi(
                [str(client), str(provider)], str(out), entry_module="mapped_client",
                backend="self", target_triple="x86_64-unknown-linux-gnu",
                ir_scaffold_mode="on", emit_llvm_only=True,
            )
        return out.read_text()


def resume(ir, name):
    return function(ir, "user_mapped_client_" + name + "__gen_resume")


class MappedFloatCodegen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = build("0")
        cls.after = build("1")

    def sites(self, name):
        return [int(site) for site in re.findall(
            r"@" + HELPER + r"\(ptr [^,\n]+, i64 ([15]), ptr [^,\n]+, ptr [^,\n)]+\)", resume(self.after, name)
        )]

    def test_only_actual_producers_at_private_numeric_consumers(self):
        self.assertCountEqual(self.sites("literal_peer"), [1, 5])
        # An early read of a restored local is not a scalar proof for site1.
        self.assertEqual(self.sites("actual_frame_local"), [5])
        self.assertEqual(self.sites("other_consumer"), [5])
        self.assertNotIn("@" + HELPER, self.before)

    def test_no_suspension_or_opaque_or_final_publication_candidate(self):
        for name in ("across_yield", "named_across_yield", "final_value", "final_sub",
                     "raw_producer", "generic_user_callback", "callback_consumer",
                     "borrowed_parameter", "conditional"):
            with self.subTest(name=name):
                self.assertEqual(self.sites(name), [])
                self.assertEqual(resume(self.after, name).count("@" + RELEASE),
                                 resume(self.before, name).count("@" + RELEASE))

    def test_miss_restores_original_owning_protocol_for_exact_slots(self):
        body = resume(self.after, "literal_peer")
        for slot, site in re.findall(r"@" + HELPER + r"\(ptr ([^,\n]+), i64 ([15]), ptr [^,\n]+, ptr [^,\n)]+\)", body):
            short = re.escape(slot)
            self.assertRegex(body, r"store ptr %[^,\n]+, ptr " + short + r"\n")
            self.assertRegex(body, r"@" + TAGGED + r"\(ptr " + short + ", i64 " + site + r"\)")
            self.assertRegex(body, r"@pcc_gc_note_slot_write_barrier\(ptr null, ptr " + short + r", ptr %[^)]+\)")
            self.assertRegex(body, r"@" + RELEASE + r"\(ptr " + short + r", i64 %call.slot.publish.lease[^)]+\)")
        for pattern in (r"@py_obj_mul_slots\(", r"@py_obj_sub\(",
                        r"@" + TAGGED + r"\(ptr .*?, i64 3\)",
                        r"@pcc_gc_pin_slot_for_take\(", r"@py_gen_finish\("):
            self.assertEqual(len(re.findall(pattern, body)),
                             len(re.findall(pattern, resume(self.before, "literal_peer"))))
