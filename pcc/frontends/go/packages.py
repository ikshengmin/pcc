"""Explicit source-package graph: no network, go tool, or stdlib substitution.

The caller supplies selected, unmodified source files for each import path.
Imports are file-scoped, checked for export visibility, and lowered together
through the same public IR. Module download/build-tag selection and package
initializers remain separate, unsupported driver/runtime boundaries.
"""

from dataclasses import dataclass

from pcc.frontends.go.lowering import Lowerer
from pcc.frontends.go.sema import Analyzer, PREDECLARED
from pcc.frontends.go.syntax import GoDiagnostic, GoParser, Program


@dataclass
class ImportBinding:
    token: object
    alias: str
    analyzer: object
    used: bool = False


def parse_package(units, *, require_main):
    units = tuple(units)
    if not units:
        raise ValueError("Go package has no source files")
    if len({filename for filename, _ in units}) != len(units):
        raise ValueError("duplicate Go source filename")
    programs = [GoParser().parse(source, filename, require_main=require_main) for filename, source in units]
    name = programs[0].token.text
    for program in programs[1:]:
        if program.token.text != name:
            raise GoDiagnostic(program.token, "source files declare different packages")
    program = Program(programs[0].token,
                      [function for item in programs for function in item.functions],
                      [constant for item in programs for constant in item.constants],
                      [imported for item in programs for imported in item.imports])
    return program


def compile_packages(packages, *, main_package, target_triple):
    if main_package not in packages:
        raise ValueError("Go main package source is missing")
    analyzed, active, order = {}, set(), []

    def visit(path, import_token=None):
        if path in active:
            raise GoDiagnostic(import_token, "import cycle involving " + path)
        if path in analyzed:
            return analyzed[path]
        if path not in packages:
            raise GoDiagnostic(import_token, "import source unavailable: " + path + "; provide original package sources")
        program = parse_package(packages[path], require_main=path == main_package)
        if path != main_package and program.token.text == "main":
            raise GoDiagnostic(program.token, "cannot import a main package")
        active.add(path)
        imports = {}
        for imported in program.imports:
            if imported.alias == "_":
                raise GoDiagnostic(imported.token, "blank imports require shared-runtime package initialization support")
            dependency = visit(imported.path, imported.token)
            alias = imported.alias or dependency.program.token.text
            if alias in PREDECLARED:
                raise GoDiagnostic(imported.token, "import alias shadows predeclared name " + alias)
            file_imports = imports.setdefault(imported.token.filename, {})
            if alias in file_imports:
                raise GoDiagnostic(imported.token, "duplicate import name " + alias)
            file_imports[alias] = ImportBinding(imported.token, alias, dependency)
        analyzer = Analyzer(program, imports=imports, require_main=path == main_package,
                            package_path=None if path == main_package else path)
        analyzer.analyze()
        active.remove(path)
        analyzed[path] = analyzer
        order.append(program)
        return analyzer

    main = visit(main_package).program
    if target_triple.split("-", 1)[0] not in ("x86_64", "aarch64", "arm64"):
        raise GoDiagnostic(main.token, "Go scalar frontend requires a supported 64-bit target")
    linked = Program(main.token, [function for program in order for function in program.functions], [])
    return Lowerer(linked, target_triple, main.token.filename).lower()
