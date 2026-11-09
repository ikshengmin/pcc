"""Explicit Go package graph and file-scoped import semantics.

https://go.dev/ref/spec#Import_declarations
https://go.dev/ref/spec#String_literals
https://go.dev/ref/spec#Package_initialization
"""

from pathlib import Path
import struct
import sys

import pytest

from pcc.frontends.go import GoDiagnostic, GoParser, UnsupportedGo, compile_go_packages, function_symbol
from tests.go.owned_execution import Execution, compile_module, run, write_artifact


FIXTURES = Path(__file__).parent / "fixtures/packages"


def fixture_packages():
    return {
        "main": [("main.go", (FIXTURES / "main.go").read_text())],
        "example/arith": [("arith.go", (FIXTURES / "arith/arith.go").read_text())],
        "example/adjust": [("adjust.go", (FIXTURES / "adjust/adjust.go").read_text())],
    }


def escaped_import_packages():
    # Authored whole graph. Preserve each source byte for the official oracle,
    # including the CR that Go discards inside the raw-string import path.
    return {
        "main": [
            ("main.go", r'''package main
import (
    "example/\x61rith"
    a "ex\u0061mple/adjust"
    . "example/\U00000064ot"
    _ "\145xample/blank"
)
func Result() int64 { return arithmetic.Value() + a.Offset + Dot() + helper() }
func main() { if Result() != 0x7edcba987654322f { for {} } }
'''),
            ("helper.go", 'package main\nimport a `example/ar\rith`\nfunc helper() int64 { return a.Small }\n'),
        ],
        "example/arith": [("arith.go", r'''package arithmetic
import a "example/\x61djust"
const Small int64 = 13
func Value() int64 { return 0x7edcba9876543210 + a.Offset }
''')],
        "example/adjust": [("adjust.go", "package adjust; const Offset int64 = 5")],
        "example/dot": [("dot.go", "package dot; func Dot() int64 { return 8 }")],
        "example/blank": [("blank.go", "package blank; const Unused = 3")],
    }


def packages(main, library="package arithmetic; const Base = 3; func Add(x int) int {return x+1}"):
    return {"main": [("main.go", main)], "lib": [("lib.go", library)]}


def test_import_alias_default_package_name_and_transitive_calls_use_public_ir():
    text = str(compile_go_packages(fixture_packages()))
    assert function_symbol("Combine", "example/arith") in text
    assert function_symbol("Result") in text
    assert "call i64" in text


@pytest.mark.parametrize("spelling", [
    '"lib"', '`lib`', r'"\x6cib"', r'"l\151b"', r'"li\u0062"', r'"\U0000006cib"', '`l\rib`',
])
def test_import_literal_decodes_before_package_lookup_without_rewriting_source(spelling):
    original = "package main; import " + spelling + "; func main(){_ = arithmetic.Base}"
    imported = GoParser().parse(original).imports[0]
    assert imported.path == "lib"
    assert imported.token.text == spelling
    assert function_symbol("main") in compile_go_packages(packages(original)).globals


@pytest.mark.parametrize("spelling,message", [
    ('""', "import path"), (r'"\x00"', "import path"), (r'"l\x01ib"', "import path"),
    (r'"l\x7fib"', "import path"), (r'"l\tib"', "import path"), (r'"l\u0020ib"', "import path"),
    (r'"l\uFFFDib"', "import path"), (r'"l\xffib"', "UTF-8"),
    (r'"l\\ib"', "import path"), (r'"l\x2aib"', "import path"),
    (r'"l\qib"', "string escape"), (r'"l\x6ib"', "string escape digits"),
    (r'"l\uD800ib"', "Unicode"), ('`l\nib`', "import path"),
])
def test_import_literal_rejects_invalid_decoded_paths_and_escapes(spelling, message, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, "lower", lambda *args: pytest.fail("invalid import reached IR"))
    with pytest.raises(GoDiagnostic, match=message) as caught:
        compile_go_packages(packages("package main; import " + spelling + "; func main(){}"))
    assert not isinstance(caught.value, UnsupportedGo)


NON_GRAPHIC_IMPORTS = (
    (r'"l\u0080ib"', "l\u0080ib"), ('"l\u0080ib"', "l\u0080ib"),  # Cc
    (r'"l\u200bib"', "l\u200bib"), ('"l\u200bib"', "l\u200bib"),  # Cf
    (r'"l\ue000ib"', "l\ue000ib"), ('"l\ue000ib"', "l\ue000ib"),  # Co
    (r'"l\u0378ib"', "l\u0378ib"), ('"l\u0378ib"', "l\u0378ib"),  # Cn
)


def non_graphic_import_packages(spelling, path):
    return {
        "main": [("main.go", "package main; import " + spelling + "; func main(){_=arithmetic.Base}")],
        path: [("lib.go", "package arithmetic; const Base=3")],
    }


@pytest.mark.parametrize("spelling,path", NON_GRAPHIC_IMPORTS)
def test_non_graphic_imports_reject_before_lookup_even_with_supplied_dependency(spelling, path, monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, "lower", lambda *args: pytest.fail("non-graphic import reached IR"))
    with pytest.raises(GoDiagnostic, match="invalid import path") as caught:
        compile_go_packages(non_graphic_import_packages(spelling, path))
    assert not isinstance(caught.value, UnsupportedGo)


def test_decoded_import_paths_keep_package_identity_and_initialization_boundaries(monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer, "lower", lambda *args: pytest.fail("invalid graph reached IR"))
    with pytest.raises(GoDiagnostic, match="duplicate import name"):
        compile_go_packages(packages(r'package main; import "lib"; import "\x6cib"; func main(){_=arithmetic.Base}'))
    with pytest.raises(GoDiagnostic, match="import cycle"):
        compile_go_packages(packages(r'package main; import "\x6cib"; func main(){_=arithmetic.Base}',
                                     r'package arithmetic; import _ "l\x69b"; const Base=3'))
    with pytest.raises(UnsupportedGo) as caught:
        compile_go_packages(packages(r'package main; import _ "\x6cib"; func main(){}',
                                     'package arithmetic; var X=f(); func f()int{return 3}'))
    assert caught.value.code == "go.unsupported.package-initialization"


def test_decoded_unsafe_path_names_the_compiler_owned_package():
    # Formerly "import source unavailable: unsafe"; no unsafe source exists.
    compile_go_packages(packages(r'package main; import _ "\x75nsafe"; func main(){}'))


def test_complete_escaped_import_graph_keeps_single_dependency_owner(monkeypatch):
    from tests.go.test_execution import forbid_external_compiler
    forbid_external_compiler(monkeypatch)
    module = compile_go_packages(escaped_import_packages())
    assert list(module.globals).count(function_symbol("Value", "example/arith")) == 1


@pytest.mark.parametrize("main,library,message", [
    ('package main; import "absent"; func main() {}', None, "import source unavailable"),
    ('package main; import "lib"; func main() {}', None, "imported and not used"),
    ('package main; import x "lib"; import x "lib"; func main(){_ = x.Base}', None, "duplicate import"),
    ('package main; import . "lib"; func main(){}', None, "imported and not used"),
    ('package main; import "lib"; func main(){_ = arithmetic.hidden()}',
     'package arithmetic; func hidden() int{return 1}', "unexported"),
    ('package main; import "lib"; func main(){_ = arithmetic.Absent()}', None, "undefined imported function"),
    ('package main; import "lib"; func main(){_ = arithmetic.Absent}', None, "undefined imported constant"),
    ('package main; import "lib"; func main(){arithmetic := 1; _ = arithmetic.Add(2)}', None, "shadowed"),
    ('package main; import "lib"; func arithmetic(){}; func main(){_ = arithmetic.Base}', None, "conflicts"),
    ('package main; import "lib"; func main(){_ = arithmetic.Add(true)}', None, "cannot use"),
    ('package main; import "lib"; func main(){_ = arithmetic.Add()}', None, "argument count"),
    ('package main; import "lib"; func main(){}', 'package main;func main(){}', "cannot import a main"),
    ('package main; import "lib"; func main(){_ = arithmetic.Base}',
     'package arithmetic; import "lib"; const Base=1', "import cycle"),
    ('package main; import "bad path"; func main(){}', None, "import path"),
    ('package main; func main(){}; import "lib"', None, "imports must precede"),
])
def test_package_graph_rejects_missing_or_invalid_semantics(main, library, message):
    inputs = packages(main) if library is None else packages(main, library)
    with pytest.raises(GoDiagnostic, match=message):
        compile_go_packages(inputs)


# Keep the exact former package-initialization boundary as a complete graph.
# test_blank_imports supplies ordinary/native/same-input reference obligations.
GRADUATED_STATIC_BLANK_IMPORT = packages('package main; import _ "lib"; func main(){}')


def test_import_names_are_file_scoped_and_not_leaked_to_sibling_files():
    inputs = packages('package main; import a "lib"; func main(){_ = a.Base}')
    inputs["main"].append(("sibling.go", "package main; func f() int {return a.Base}"))
    with pytest.raises(GoDiagnostic, match="undefined imported package"):
        compile_go_packages(inputs)


def test_renaming_import_in_another_file_is_independent():
    inputs = packages('package main; import a "lib"; func main(){_ = a.Base; _ = helper()}')
    inputs["main"].append(("helper.go", 'package main; import b "lib"; func helper() int {return b.Base}'))
    assert "call i64" in str(compile_go_packages(inputs))


def test_each_file_in_a_package_must_declare_the_same_name():
    inputs = packages("package main;func main(){}")
    inputs["main"].append(("other.go", "package other;func f(){}"))
    with pytest.raises(GoDiagnostic, match="different packages|requires package main"):
        compile_go_packages(inputs)


def test_main_entry_symbol_does_not_collide_with_library_import_path_named_main():
    inputs = {
        "application": [("app.go", 'package main; import "main"; func main(){_ = library.F()}')],
        "main": [("library.go", 'package library; func F() int{return 1}')],
    }
    text = str(compile_go_packages(inputs, main_package="application"))
    assert function_symbol("main") in text
    assert function_symbol("F", "main") in text
    assert function_symbol("F") not in text


def test_original_standard_package_now_reaches_its_real_type_frontier():
    bits = Path(__file__).parent / "upstream/go1.27.3/src/math/bits/bits.go"
    inputs = {
        "main": [("main.go", 'package main; import "math/bits"; func main(){_ = bits.LeadingZeros(1)}')],
        "math/bits": [("bits.go", bits.read_text())],
    }
    with pytest.raises(GoDiagnostic, match="undefined name ntz8tab") as failure:
        # The unchanged complete file now parses its local grouped constants.
        # Its original string-table dependency is a separate source unit.
        compile_go_packages(inputs)
    assert failure.value.filename == "bits.go" and failure.value.line == 68


@pytest.mark.integration
@pytest.mark.parametrize("observe_result", [False, True])
def test_cross_package_calls_execute_through_owned_ir_backend_and_elf(tmp_path, observe_result):
    assert sys.platform == "linux", "real Linux execution is required"
    artifact = compile_module(compile_go_packages(fixture_packages()), observe_result=observe_result)
    binary = tmp_path / "package-program"
    write_artifact(artifact, binary)
    expected = struct.pack("<q", 2109) if observe_result else b""
    assert run(binary) == Execution(0, expected, b"")


@pytest.mark.integration
@pytest.mark.parametrize("observe_result", [False, True])
def test_escaped_import_graph_executes_full_width_owned_result(tmp_path, monkeypatch, observe_result):
    from tests.go.test_execution import forbid_external_compiler
    from tests.go.test_labels import observed
    assert sys.platform == "linux", "real Linux execution is required"
    inputs = escaped_import_packages()
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact = compile_module(compile_go_packages(inputs), observe_result=observe_result)
    binary = tmp_path / "package-program"
    write_artifact(artifact, binary)
    expected = struct.pack("<q", 0x7edcba987654322f) if observe_result else b""
    originals = tuple((package + "/" + name, text) for package, files in inputs.items() for name, text in files)
    assert observed(binary, tmp_path / "observation", owner=artifact.owner, originals=originals) == Execution(0, expected, b"")


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
def test_original_package_sources_match_the_official_go_oracle(tmp_path, monkeypatch):
    import os
    from tests.go.owned_execution import compile_reference_packages, compile_reference_packages_rejection
    from tests.go.test_execution import forbid_external_compiler
    from tests.go.test_labels import observed

    assert os.environ.get("PCC_GO_REFERENCE_CACHE"), "select a bounded oracle cache explicitly"
    for name, graph in (("original", fixture_packages), ("escaped", escaped_import_packages)):
        directory = tmp_path / name
        directory.mkdir()
        inputs = graph()
        reference = compile_reference_packages(inputs, directory / "reference",
            compiler=os.environ["PCC_GO_REFERENCE"], cache=os.environ["PCC_GO_REFERENCE_CACHE"])
        owned = directory / "owned"
        with monkeypatch.context() as denied:
            forbid_external_compiler(denied)
            artifact = compile_module(compile_go_packages(inputs))
        write_artifact(artifact, owned)
        originals = tuple((package + "/" + name, text) for package, files in inputs.items() for name, text in files)
        actual = observed(owned, directory / "owned-observation", owner=artifact.owner, originals=originals)
        expected = observed(reference, directory / "reference-observation", owner="official-go", originals=originals)
        assert actual == expected == Execution(0, b"", b"")
        for package, source_units in inputs.items():
            for filename, source in source_units:
                copied = reference.parent / "gopath/src" / package / filename
                assert copied.read_bytes() == source.encode("utf-8")
    # Invalid import paths must be rejected before dependency lookup, even
    # when the exact named package exists. Keep both escaped/literal spellings.
    for index, (spelling, path) in enumerate(NON_GRAPHIC_IMPORTS):
        inputs = non_graphic_import_packages(spelling, path)
        with pytest.raises(GoDiagnostic, match="invalid import path"):
            compile_go_packages(inputs)
        directory = tmp_path / ("non-graphic-" + str(index))
        compile_reference_packages_rejection(inputs, directory,
            compiler=os.environ["PCC_GO_REFERENCE"], cache=os.environ["PCC_GO_REFERENCE_CACHE"],
            diagnostics="invalid import path|invalid character")
        for package, files in inputs.items():
            for filename, source in files:
                assert (directory / "gopath/src" / package / filename).read_bytes() == source.encode("utf-8")


def test_parenthesized_package_name_is_not_an_ordinary_value():
    inputs = packages('package main; import "lib"; func main(){_ = (arithmetic).Base}')
    with pytest.raises(GoDiagnostic, match="unparenthesized imported package"):
        compile_go_packages(inputs)


PACKAGE_DECLARATION_CONFLICTS = (
    "const arithmetic = 7",
    "func arithmetic() int { return 7 }",
)


def conflicting_sibling_package(declaration):
    return {
        "main": [
            ("a.go", 'package main; import arithmetic "lib"; func main(){_ = arithmetic.Base}'),
            ("b.go", "package main; " + declaration),
        ],
        "lib": [("lib.go", "package arithmetic; const Base = 3")],
    }


@pytest.mark.parametrize("declaration", PACKAGE_DECLARATION_CONFLICTS)
def test_import_alias_conflicts_with_sibling_file_package_declaration(declaration):
    with pytest.raises(GoDiagnostic, match="import name conflicts with package declaration"):
        compile_go_packages(conflicting_sibling_package(declaration))


def test_init_is_reserved_in_package_block_but_allowed_in_function_block():
    with pytest.raises(GoDiagnostic, match="reserved for init function"):
        compile_go_packages({"main": [("main.go", "package main; const init=1; func main(){}") ]})
    assert "ret void" in str(compile_go_packages({
        "main": [("main.go", "package main; func main(){const init=1; _=init}")],
    }))


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
@pytest.mark.parametrize("declaration", PACKAGE_DECLARATION_CONFLICTS)
def test_official_go_rejects_same_sibling_file_import_conflict(tmp_path, declaration):
    import os
    from tests.go.owned_execution import compile_reference_packages_rejection

    assert os.environ.get("PCC_GO_REFERENCE_CACHE"), "select a bounded oracle cache explicitly"
    inputs = conflicting_sibling_package(declaration)
    compile_reference_packages_rejection(
        inputs, tmp_path / "reference", compiler=os.environ["PCC_GO_REFERENCE"],
        cache=os.environ["PCC_GO_REFERENCE_CACHE"],
        diagnostics="arithmetic already declared through import",
    )
    for package, source_units in inputs.items():
        for filename, original in source_units:
            assert (tmp_path / "reference/gopath/src" / package / filename).read_bytes() == original.encode("utf-8")
