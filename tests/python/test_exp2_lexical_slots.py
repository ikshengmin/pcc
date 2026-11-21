"""Private experiment: emitted scope comes from exact compiler-owned records."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.python.test_gc3_ceiling_experiment import function, generated


PUBLICATION = "pcc_exp2_lexical_publish_slot"
TAKE = "pcc_gc_frame_leave_lifo_exp2_take"
SOURCE = """def f(x: list[str], sep: str) -> str:
    return sep.join(x)
"""
OPAQUE = """from pcc.extern import c_obj, extern
foreign = extern("fixture_unknown_owner", (), c_obj)
def f() -> object:
    return foreign()
"""


class Exp2LexicalSlots(unittest.TestCase):
    def code(self, src, on):
        with patch.dict(os.environ, {"PCC_EXPERIMENT_GC3_LEXICAL": "1" if on else "0"}):
            return function(generated(src, "lexical_test", enabled=False), "user_lexical_test_f")

    def test_proved_publication_and_take_keep_same_original_paths_for_miss(self):
        candidate = self.code(SOURCE, True)
        baseline = self.code(SOURCE, False)
        self.assertNotIn(PUBLICATION, baseline)
        self.assertNotIn(TAKE, baseline)
        self.assertIn("call i64 (ptr) @" + PUBLICATION, candidate)
        self.assertIn("call ptr (ptr) @" + TAKE, candidate)
        for abi in ("pcc_gc_foreign_lease_acquire","pcc_gc_foreign_lease_release",
                    "pcc_gc_pin_slot_for_take","pcc_gc_take_pinned_slot"):
            self.assertGreaterEqual(candidate.count("@"+abi+"("), baseline.count("@"+abi+"("))
        self.assertLess(candidate.index("@py_str_join"), candidate.index("@"+PUBLICATION))
        self.assertLess(candidate.index("@"+TAKE), candidate.index("@pcc_gc_pin_slot_for_take"))

    def test_opaque_return_keeps_the_old_exposure_both_ways(self):
        candidate = self.code(OPAQUE, True)
        baseline = self.code(OPAQUE, False)
        self.assertIn("call ptr () @fixture_unknown_owner", candidate)
        self.assertNotIn(PUBLICATION, candidate)
        self.assertNotIn(TAKE, candidate)
        self.assertEqual(candidate, baseline)

    def test_runtime_port_has_no_compiler_proof(self):
        src = "__pcc_runtime_port__ = True\n" + SOURCE
        self.assertEqual(self.code(src, True), self.code(src, False))

    def test_source_owned_clock_publication_is_distinct_from_opaque_abi(self):
        clock = OPAQUE.replace("fixture_unknown_owner", "py_time_perf_counter")
        baseline = self.code(clock, False)
        selected = self.code(clock, True)
        self.assertNotIn(PUBLICATION, baseline)
        self.assertIn("call ptr () @py_time_perf_counter", selected)
        self.assertIn("@" + PUBLICATION + "(", selected)
        self.assertIn("@" + TAKE + "(", selected)

    def test_real_generator_import_has_lexical_result_only_without_resume_edge(self):
        from pcc.frontends.python.pipeline import compile_python_multi

        with tempfile.TemporaryDirectory(prefix="pcc-exp2-lexical-") as tmp:
            work = Path(tmp)
            provider = work / "lex_provider.py"
            provider.write_text("def reply(value: str) -> str:\n    return value\n")
            client = work / "lex_gen.py"
            client.write_text(
                "from lex_provider import reply\n"
                "\ndef f(value: str):\n    yield 0\n    return reply(value).encode()\n"
                "\ndef g(value: str):\n    yield 0\n    return reply((yield value)).encode()\n"
            )
            for selected in ("0", "1"):
                output = work / ("lex-" + selected + ".ll")
                with patch.dict(os.environ, {"PCC_EXPERIMENT_GC3_LEXICAL": selected}):
                    compile_python_multi(
                        [str(client), str(provider)], str(output), backend="self",
                        entry_module="lex_gen", ir_scaffold_mode="on", emit_llvm_only=True,
                    )
                llvm = output.read_text()
                synchronous = function(llvm, "user_lex_gen_f__gen_resume")
                suspended = function(llvm, "user_lex_gen_g__gen_resume")
                candidate_slot = [line for line in synchronous.splitlines()
                                  if "reply.import.result.operand" in line]
                self.assertTrue(candidate_slot)
                if selected == "1":
                    for abi in ("pcc_gc_frame_enter_lifo_local", PUBLICATION, TAKE):
                        self.assertTrue(any("@" + abi + "(" in line for line in candidate_slot), abi)
                else:
                    self.assertFalse(any("@" + PUBLICATION + "(" in line or "@" + TAKE + "(" in line
                                         for line in candidate_slot))
                for line in suspended.splitlines():
                    if "reply.import.result.operand" in line:
                        self.assertNotIn("@pcc_exp2_lexical_", line)
