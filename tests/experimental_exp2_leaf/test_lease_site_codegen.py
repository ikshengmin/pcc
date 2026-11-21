"""M9 diagnostic: lexical/ordinary ABI shape and fixed ownership site IDs."""
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from tests.python.test_gc3_ceiling_experiment import function, generated

FLAG = "PCC_EXPERIMENT_GC3_LEASE_SITES"
SPECIAL = "pcc_exp2_foreign_lease_acquire_site"
REGULAR = "pcc_gc_foreign_lease_acquire"


class LeaseSiteCodegen(unittest.TestCase):
    def emit(self, src, flag):
        with patch.dict(os.environ, {FLAG: flag, "PCC_EXPERIMENT_GC3_LEXICAL": "1"}):
            return function(generated(src, "lease_sites", enabled=False), "user_lease_sites_f")

    def test_disabled_arms_and_opaque_owner_use_original_calls(self):
        source = "def f(values: list[str], sep: str) -> str:\n    return sep.join(values)\n"
        absent = self.emit(source, "")
        disabled = self.emit(source, "0")
        self.assertEqual(absent, disabled)
        self.assertIn("@" + REGULAR, disabled)
        self.assertNotIn("@" + SPECIAL, disabled)
        active = self.emit(source, "1")
        self.assertRegex(active, r"@" + SPECIAL + r"\(ptr .*?, i64 6\)")
        self.assertRegex(active, r"@" + SPECIAL + r"\(ptr .*?, i64 3\)")
        self.assertEqual(active.count("@pcc_gc_foreign_lease_release("),
                         disabled.count("@pcc_gc_foreign_lease_release("))
        foreign = ("from pcc.extern import c_obj, extern\n"
                   'opaque = extern("unknown_c_owner", (), c_obj)\n'
                   "def f() -> object:\n    return opaque()\n")
        self.assertEqual(self.emit(foreign, "1"), self.emit(foreign, "0"))

    def test_actual_generator_registered_persistent_sites(self):
        from pcc.frontends.python.pipeline import compile_python_multi

        with tempfile.TemporaryDirectory(prefix="m9-lease-sites-") as t:
            p = Path(t)
            provider = p / "lease_provider.py"
            source = p / "lease_client.py"
            ir = p / "built.ll"
            provider.write_text("def tick() -> float:\n    return 42.25\n")
            source.write_text(
                "from lease_provider import tick\n\n"
                "def f(value):\n    yield 0\n    return (tick() - value) * 1000.0\n"
            )
            with patch.dict(os.environ, {FLAG: "1", "PCC_EXPERIMENT_GC3_LEXICAL": "1"}):
                compile_python_multi(
                    [str(source), str(provider)], str(ir), entry_module="lease_client",
                    backend="self", ir_scaffold_mode="on", emit_llvm_only=True,
                )
            body = function(ir.read_text(), "user_lease_client_f__gen_resume")
            tagged = [line for line in body.splitlines() if "@" + SPECIAL + "(" in line]
            self.assertTrue(any("binary.object.left.left.operand" in l and
                                re.search(r", i64 1\)", l) for l in tagged), tagged)
            self.assertTrue(any("binary.object.left.left.operand" in l and
                                re.search(r", i64 3\)", l) for l in tagged), tagged)
            self.assertTrue(any("binary.object.left.result.operand" in l and
                                re.search(r", i64 5\)", l) for l in tagged), tagged)
