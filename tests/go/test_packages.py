"""Explicit Go package graph and file-scoped import semantics."""

from pathlib import Path
import struct
import sys

import pytest

from pcc.frontends.go import GoDiagnostic, compile_go_packages, function_symbol
from tests.go.owned_execution import Execution, compile_module, run, write_artifact


FIXTURES = Path(__file__).parent / "fixtures/packages"


def fixture_packages():
    return {
        "main": [("main.go", (FIXTURES / "main.go").read_text())],
        "example/arith": [("arith.go", (FIXTURES / "arith/arith.go").read_text())],
        "example/adjust": [("adjust.go", (FIXTURES / "adjust/adjust.go").read_text())],
    }


def packages(main, library="package arithmetic; const Base = 3; func Add(x int) int {return x+1}"):
    return {"main": [("main.go", main)], "lib": [("lib.go", library)]}


def test_import_alias_default_package_name_and_transitive_calls_use_public_ir():
    text = str(compile_go_packages(fixture_packages()))
    assert function_symbol("Combine", "example/arith") in text
    assert function_symbol("Result") in text
    assert "call i64" in text


@pytest.mark.parametrize("main,library,message", [
    ('package main; import "absent"; func main() {}', None, "import source unavailable"),
    ('package main; import "lib"; func main() {}', None, "imported and not used"),
    ('package main; import x "lib"; import x "lib"; func main(){_ = x.Base}', None, "duplicate import"),
    ('package main; import _ "lib"; func main(){}', None, "initialization support"),
    ('package main; import . "lib"; func main(){}', None, "dot imports"),
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
    with pytest.raises(GoDiagnostic, match=r"expected 'name', got '\('") as failure:
        # Original bits.go now parses named/multiple results, but its original
        # Div64 local grouped constant declaration is the next syntax boundary.
        compile_go_packages(inputs)
    assert failure.value.filename == "bits.go" and failure.value.line == 534


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
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
def test_original_package_sources_match_the_official_go_oracle(tmp_path):
    import os
    from tests.go.owned_execution import compile_reference_packages

    assert os.environ.get("PCC_GO_REFERENCE_CACHE"), "select a bounded oracle cache explicitly"
    inputs = fixture_packages()
    reference = compile_reference_packages(inputs, tmp_path / "reference",
        compiler=os.environ["PCC_GO_REFERENCE"], cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    owned = tmp_path / "owned"
    write_artifact(compile_module(compile_go_packages(inputs)), owned)
    assert run(owned) == run(reference) == Execution(0, b"", b"")
    for package, source_units in inputs.items():
        for filename, source in source_units:
            copied = reference.parent / "gopath/src" / package / filename
            assert copied.read_bytes() == source.encode("utf-8")


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
    import subprocess

    assert os.environ.get("PCC_GO_REFERENCE_CACHE"), "select a bounded oracle cache explicitly"
    inputs = conflicting_sibling_package(declaration)
    gopath = tmp_path / "gopath"
    for package, source_units in inputs.items():
        directory = gopath / "src" / package
        directory.mkdir(parents=True)
        for filename, original in source_units:
            (directory / filename).write_bytes(original.encode("utf-8"))
    env = dict(os.environ, GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off",
               GOWORK="off", GO111MODULE="off", CGO_ENABLED="0", GOMAXPROCS="2",
               GOCACHE=os.environ["PCC_GO_REFERENCE_CACHE"], GOPATH=str(gopath))
    output = tmp_path / "must-not-exist"
    result = subprocess.run([os.environ["PCC_GO_REFERENCE"], "build", "-p=2", "-o", str(output),
                             str(gopath / "src/main/a.go"), str(gopath / "src/main/b.go")],
                            env=env, capture_output=True, timeout=60, cwd=tmp_path)
    assert result.returncode != 0
    assert b"arithmetic already declared through import" in result.stderr, result.stderr
    assert not output.exists()
    for package, source_units in inputs.items():
        for filename, original in source_units:
            assert (gopath / "src" / package / filename).read_bytes() == original.encode("utf-8")
