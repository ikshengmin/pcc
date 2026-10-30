"""Development-reference AST inventory tests; never provision native pcc1."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cpp_feature_inventory.py"
SPEC = importlib.util.spec_from_file_location("cpp_feature_inventory", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


@unittest.skipUnless(os.environ.get("PCC_CPP_INVENTORY_LIBCLANG"),
                     "set PCC_CPP_INVENTORY_LIBCLANG to an existing libclang")
class CppFeatureInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.clang = inventory.Clang(os.environ["PCC_CPP_INVENTORY_LIBCLANG"])

    def tearDown(self):
        self.temporary.cleanup()

    def parse(self, text):
        source = self.root / "input.cpp"
        source.write_text(text)
        return self.clang.parse(source, ["-x", "c++", "-std=c++17"])

    def test_real_cpp17_nodes_and_declaration_only_operator_count(self):
        source = """
namespace sample {
template<class T> struct Trait {};
template<class T> struct Trait<T*> {};
template<class T, int N> struct Box {
  T value;
  Box(T input) : value(input) {}
  ~Box() {}
  T operator()() const { return value; }
};
template<class T, class... Rest> constexpr int choose(T, Rest...) {
  if constexpr (sizeof...(Rest) == 0) return 1;
  return 2;
}
int example() {
  constexpr int count = 3;
  Box<int, count> box(4);
  auto lambda = [](int value) { return value + 1; };
  using Closure = decltype(lambda);
  int values[] = {1, 2};
  int total = 0;
  for (int value : values) total += value;
  try { throw 3; } catch (int error) { total += error; }
  static_assert(sizeof(int) > 1, "integer");
  return static_cast<int>(lambda(box()) + choose(total));
}
}
"""
        result = self.parse(source)
        self.assertEqual(result["ast_status"], "parsed", result["diagnostics"])
        expected = {
            "namespace", "class_template", "class_template_partial_specialization",
            "function_template", "constructor", "destructor", "operator_overload",
            "lambda", "range_for", "try_statement", "catch_statement",
            "throw_expression", "static_assert", "static_cast", "if_constexpr",
            "parameter_pack", "constexpr_variable", "decltype_type_alias",
        }
        self.assertFalse(expected - result["features"].keys())
        self.assertEqual(result["features"]["operator_overload"]["nodes"], 1)
        self.assertEqual(result["source_sha256"], hashlib.sha256(source.encode()).hexdigest())

    def test_missing_header_preserves_diagnostics_and_partial_ast(self):
        result = self.parse('#include "missing.h"\n'
                            'template<class T> T identity(T value) { return value; }\n')
        self.assertEqual(result["ast_status"], "partial")
        self.assertTrue(any(d["severity"] >= 3 for d in result["diagnostics"]))
        self.assertTrue(any("missing.h" in d["text"] for d in result["diagnostics"]))
        self.assertIn("function_template", result["features"])

    def test_comments_strings_and_included_headers_are_not_main_file_features(self):
        (self.root / "header.h").write_text('template<class T> struct HeaderOnly {};\n')
        result = self.parse('#include "header.h"\n'
                            '// template<class T> class Fake {}; try {} catch (...) {}\n'
                            'const char *words = "template namespace throw constexpr";\n'
                            'int plain() { return 0; }\n')
        self.assertEqual(result["ast_status"], "parsed", result["diagnostics"])
        self.assertEqual(result["features"], {})

    def test_explicit_roots_include_project_and_generated_headers_not_external(self):
        project, build, external = (self.root / name for name in ("project", "build", "project-extra"))
        for directory in (project, build, external):
            directory.mkdir()
        header = project / "owned.hpp"
        header.write_text('template<class T> struct Owned { ~Owned() {} };\n')
        (build / "generated.hpp").write_text('template<class T> struct Generated {};\n')
        outside = external / "external.hpp"
        outside.write_text('template<class T> struct External {};\n')
        (project / "linked.hpp").symlink_to(outside)
        source = self.root / "input.cpp"
        source.write_text('#include "project/owned.hpp"\n#include "build/generated.hpp"\n'
                          '#include "project/linked.hpp"\nint value;\n')
        result = self.clang.parse(source, ["-x", "c++", "-std=c++17"],
                                  include_roots=[project, build])
        self.assertEqual(result["ast_status"], "parsed", result["diagnostics"])
        sites = result["feature_sites"]["class_template"]
        self.assertEqual({site["spelling"] for site in sites}, {"Owned", "Generated"})
        self.assertEqual({site["file"] for site in sites},
                         {str(header), str(build / "generated.hpp")})
        self.assertNotIn(str(outside), result["observed_files"])
        self.assertEqual(result["observed_files"][str(header)],
                         hashlib.sha256(header.read_bytes()).hexdigest())
        self.assertEqual(result["feature_sites"]["destructor"][0]["file"], str(header))

    def project_reports(self):
        project = self.root / "project"
        project.mkdir()
        (project / "shared.hpp").write_text("".join(
            f"template<class T> struct Shared{index} {{}};\n" for index in range(12)))
        reports = []
        for name in ("first", "second"):
            source = self.root / f"{name}.cpp"
            source.write_text('#include "project/shared.hpp"\n'
                              f'namespace {name} {{ int value; }}\n')
            reports.append(self.clang.parse(source, ["-x", "c++", "-std=c++17"],
                                            include_roots=[project]))
        return reports

    def test_merge_uses_all_sites_not_five_location_samples_or_tu_node_sum(self):
        reports = self.project_reports()
        self.assertEqual(len(reports[0]["features"]["class_template"]["locations"]), 5)
        self.assertEqual(len(reports[0]["feature_sites"]["class_template"]), 12)
        merged = inventory.merge_reports(reports)
        self.assertEqual(merged["ast_status"], "parsed")
        self.assertEqual(merged["features"]["class_template"]["source_sites"], 12)
        self.assertEqual(merged["features"]["class_template"]["files"], 1)
        self.assertEqual(merged["features"]["namespace"]["source_sites"], 2)
        self.assertFalse(merged["is_owned_pcc1_execution"])

    def test_merge_rejects_changed_header_identity_and_main_only_reports(self):
        reports = self.project_reports()
        header = str(self.root / "project/shared.hpp")
        reports[1]["observed_files"][header] = "0" * 64
        with self.assertRaisesRegex(ValueError, "source identity changed"):
            inventory.merge_reports(reports)
        with self.assertRaisesRegex(ValueError, "explicit include roots"):
            inventory.merge_reports([self.parse("int value;")])

    def test_merge_partial_and_unavailable_inputs_never_become_parsed(self):
        reports = self.project_reports()
        source = self.root / "missing.cpp"
        source.write_text('#include "missing.hpp"\n#include "project/shared.hpp"\n')
        partial = self.clang.parse(source, ["-x", "c++", "-std=c++17"],
                                   include_roots=[self.root / "project"])
        self.assertEqual(partial["ast_status"], "partial")
        merged = inventory.merge_reports([*reports, partial])
        self.assertEqual(merged["ast_status"], "partial")
        self.assertEqual(merged["translation_unit_statuses"], {"parsed": 2, "partial": 1})
        unavailable = dict(partial, ast_status="unavailable", feature_sites={})
        self.assertEqual(inventory.merge_reports([*reports, unavailable])["ast_status"], "unavailable")

    def test_cli_explicit_include_root_and_merge_without_loading_libclang(self):
        project = self.root / "project"
        project.mkdir()
        (project / "header.hpp").write_text('template<class T> struct Header {};\n')
        source, output = self.root / "input.cpp", self.root / "report.json"
        source.write_text('#include "project/header.hpp"\n')
        process = subprocess.run(
            [sys.executable, str(SCRIPT), "--libclang",
             os.environ["PCC_CPP_INVENTORY_LIBCLANG"], "--source", str(source),
             "--include-root", str(project), "--output", str(output),
             "--", "-x", "c++", "-std=c++17"],
            check=False, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        merged_output = self.root / "merged.json"
        process = subprocess.run(
            [sys.executable, str(SCRIPT), "--merge", str(output), str(output),
             "--output", str(merged_output)],
            check=False, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        merged = json.loads(merged_output.read_text())
        self.assertEqual(merged["features"]["class_template"]["source_sites"], 1)
        self.assertEqual(merged["report_inputs"][0]["sha256"],
                         hashlib.sha256(output.read_bytes()).hexdigest())

    def test_callback_failure_breaks_scan_and_propagates_after_native_visit(self):
        failure = RuntimeError("injected cursor observer failure")
        lib = self.clang.lib
        with (
            mock.patch.object(lib, "clang_getCursorKindSpelling", side_effect=failure) as observer,
            mock.patch.object(lib, "clang_disposeTranslationUnit",
                              wraps=lib.clang_disposeTranslationUnit) as dispose_unit,
            mock.patch.object(lib, "clang_disposeIndex",
                              wraps=lib.clang_disposeIndex) as dispose_index,
        ):
            with self.assertRaisesRegex(RuntimeError, "AST feature collection failed") as caught:
                self.parse("int first() { return 1; }\nint second() { return 2; }\n")
            self.assertIs(caught.exception.__cause__, failure)
            observer.assert_called_once()
            dispose_unit.assert_called_once()
            dispose_index.assert_called_once()

    def test_nonzero_parse_result_with_real_unit_is_never_a_parse_pass(self):
        lib = self.clang.lib
        parse_native = lib.clang_parseTranslationUnit2

        def failed_result_with_valid_unit(*args):
            self.assertEqual(parse_native(*args), 0)
            return 1

        with (
            mock.patch.object(lib, "clang_parseTranslationUnit2",
                              side_effect=failed_result_with_valid_unit),
            mock.patch.object(lib, "clang_getNumDiagnostics") as get_diagnostics,
            mock.patch.object(lib, "clang_disposeTranslationUnit",
                              wraps=lib.clang_disposeTranslationUnit) as dispose_unit,
            mock.patch.object(lib, "clang_disposeIndex",
                              wraps=lib.clang_disposeIndex) as dispose_index,
        ):
            result = self.parse("int valid() { return 1; }\n")
            self.assertEqual(result["parse_error"], 1)
            self.assertEqual(result["ast_status"], "unavailable")
            self.assertEqual(result["features"], {})
            get_diagnostics.assert_not_called()
            dispose_unit.assert_called_once()
            dispose_index.assert_called_once()

    def test_null_index_is_rejected_without_calling_parser_or_disposal(self):
        lib = self.clang.lib
        with (
            mock.patch.object(lib, "clang_createIndex", return_value=None),
            mock.patch.object(lib, "clang_parseTranslationUnit2") as parse_native,
            mock.patch.object(lib, "clang_disposeIndex") as dispose_index,
        ):
            with self.assertRaisesRegex(RuntimeError, "could not create an index"):
                self.parse("int value;\n")
            parse_native.assert_not_called()
            dispose_index.assert_not_called()

    def test_raised_parse_call_cleans_index_and_any_allocated_unit(self):
        lib = self.clang.lib
        parse_native = lib.clang_parseTranslationUnit2
        for allocate_unit in (False, True):
            with self.subTest(allocate_unit=allocate_unit):
                def raise_during_parse(*args):
                    if allocate_unit:
                        self.assertEqual(parse_native(*args), 0)
                    raise RuntimeError("injected parse failure")

                with (
                    mock.patch.object(lib, "clang_parseTranslationUnit2",
                                      side_effect=raise_during_parse),
                    mock.patch.object(lib, "clang_disposeTranslationUnit",
                                      wraps=lib.clang_disposeTranslationUnit) as dispose_unit,
                    mock.patch.object(lib, "clang_disposeIndex",
                                      wraps=lib.clang_disposeIndex) as dispose_index,
                ):
                    with self.assertRaisesRegex(RuntimeError, "injected parse failure"):
                        self.parse("int value;\n")
                    self.assertEqual(dispose_unit.call_count, int(allocate_unit))
                    dispose_index.assert_called_once()

    def test_cli_partial_exit_and_explicit_non_owned_label(self):
        source, output = self.root / "missing.cpp", self.root / "report.json"
        source.write_text('#include "missing.h"\nint value;\n')
        process = subprocess.run(
            [sys.executable, str(SCRIPT), "--libclang",
             os.environ["PCC_CPP_INVENTORY_LIBCLANG"], "--source", str(source),
             "--output", str(output), "--", "-x", "c++", "-std=c++17"],
            check=False, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(process.returncode, 2, process.stderr)
        result = json.loads(output.read_text())
        self.assertEqual(result["ast_status"], "partial")
        self.assertFalse(result["is_owned_pcc1_execution"])
        self.assertEqual(result["analysis_owner"], "development-reference-libclang")
        self.assertEqual(result["schema"], "pcc.developer-cpp-feature-inventory.v1")


if __name__ == "__main__":
    unittest.main()
