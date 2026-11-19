"""Contract of the ordinary production closure scanner, without a compiler run."""
import unittest

from pcc.frontends.python import pipeline_import_scan as scan


class NoAliasTypeCheckingScanTests(unittest.TestCase):
    def test_no_alias_no_semantic_change(self):
        cases = (
            "", " ", "from typing import Optional", "if False: import unreachable",
            "if (TYPE_CHECKING):\n    import NOT_MASKED\n",
            "if missing.TYPE_CHECKING:\n    from pkg import still_here",
            "if (((unknown))): some()\nif True: yes()\nif 0: stop()\n",
            "# from typing import TYPE_CHECKING as TC\nif TC: import untouched\n",
            "'import typing as t'\nif t.TYPE_CHECKING: import untouched\n",
            "é = '漢字'\nif FLAG: pass\n\n",
            "x = 1\nif not TYPE_CHECKING: live()",
            "x = '\ud800'\nif …: pass\x00",
        )
        for source in cases:
            with self.subTest(source=source):
                out = scan._without_type_checking_imports_uncached(source)
                expected = "".join(source.splitlines(keepends=True))
                self.assertEqual(out, expected)
                self.assertEqual(out is source, expected is source)

    def test_every_splitlines_separator_and_repeated_empty_lines(self):
        for sep in ("\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e",
                    "\x85", "\u2028", "\u2029"):
            for source in (sep, "λ" + sep, "λ" + sep + sep + "end", sep.join((
                "from typing import Optional", "if UNKNOWN:", "    import live", "",
            ))):
                with self.subTest(sep=repr(sep), length=len(source)):
                    actual = scan._without_type_checking_imports_uncached(source)
                    expected = "".join(source.splitlines(keepends=True))
                    self.assertEqual(actual.encode("utf-8"), expected.encode("utf-8"))
                    self.assertEqual(actual is source, expected is source)

    def test_direct_module_and_symbol_aliases_still_mask_only_their_guard(self):
        entry = scan._without_type_checking_imports_uncached
        source = ("import typing as ty\nif ((ty.TYPE_CHECKING)):\n"
                  "    from helper import Type\n    import helper2\n"
                  "if False: live()\n")
        self.assertEqual(entry(source),
                         "import typing as ty\nif ((ty.TYPE_CHECKING)):\n"
                         "    pass\n\nif False: live()\n")
        source = ("from typing import (\n    Optional,\n    TYPE_CHECKING as FLAG,\n)\n"
                  "if FLAG: import hidden\nif OTHER: import visible")
        self.assertEqual(entry(source), source.replace("if FLAG: import hidden", "if FLAG: pass"))
        source = "import typing\nif typing.TYPE_CHECKING:\n    from p import q\nkeep()\n"
        self.assertEqual(entry(source), source.replace("    from p import q", "    pass"))

    def test_alias_declared_without_guard_keeps_original_bytes(self):
        for source in ("import typing as t\nif False: import live\n",
                       "from typing import TYPE_CHECKING as Tc\nif WRONG: import kept\r\n"):
            self.assertEqual(scan._without_type_checking_imports_uncached(source), source)

    def test_exact_cache_behavior_and_public_production_composition(self):
        a = "".join(("if CLOSURE_TYPEGUARD_TEST_FLAG:\n", "    import kept\n"))
        b = "".join(("if CLOSURE_TYPEGUARD_TEST_FLAG:\n", "    import kept\n"))
        self.assertIsNot(a, b)
        out = scan._without_inactive_runtime_imports(a)
        self.assertEqual(out, a)
        self.assertIn(a, scan._DISCOVERY_TEXT_CACHE)
        self.assertIn(a, scan._WITHOUT_TYPE_CHECKING_CACHE)
        self.assertIs(scan._without_inactive_runtime_imports(b), out)

    def test_custom_string_splitlines_callbacks_and_errors_remain_at_old_stages(self):
        class Tracked(str):
            calls = None
            def splitlines(self, keepends=False):
                self.calls.append(keepends)
                return str.splitlines(self, keepends)
        source = Tracked("if CLOSURE_SUBCLASS_CALLBACK_UNIQUE: import kept\n")
        source.calls = []
        self.assertEqual(scan._without_type_checking_imports_uncached(source), str(source))
        self.assertEqual(source.calls, [False, True])

        class FailsOnSecond(str):
            def splitlines(self, keepends=False):
                if keepends:
                    raise ValueError("second-scan callback")
                return str.splitlines(self, keepends)
        with self.assertRaisesRegex(ValueError, "second-scan callback"):
            scan._without_type_checking_imports_uncached(
                FailsOnSecond("if CLOSURE_SUBCLASS_ERROR_UNIQUE: pass\n")
            )


if __name__ == "__main__":
    unittest.main()
