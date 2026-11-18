"""Host-development C, C++ and archive drivers for the PCC Meson adapter.

Unsupported options are errors. These commands never delegate target code to
an external compiler, assembler, linker, archiver or math library.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sys
import tempfile


_SOURCE = {"c": (".c",), "cpp": (".cc", ".cpp", ".cxx", ".C", ".c++")}


def release() -> str:
    import tomllib
    root = Path(__file__).resolve().parents[2]
    config = root / "pyproject.toml"
    if config.is_file():
        project = tomllib.loads(config.read_text(encoding="utf-8"))["project"]
        if project["name"] != "python-cc":
            raise ValueError("invalid PCC source release metadata")
        return project["version"]
    from importlib.metadata import version
    return version("python-cc")


def banner(tool: str) -> str:
    if tool in _SOURCE:
        return f"PCC {tool} compiler {release()}"
    if tool == "ar":
        return f"PCC indexed archiver {release()}"
    raise ValueError("expected a PCC c, cpp or ar tool")


def _protect_output(output: Path, inputs) -> None:
    for path in inputs:
        if output.resolve() == path.resolve() or (output.exists() and path.exists() and output.samefile(path)):
            raise ValueError(f"compiler output aliases protected input: {path}")
    if output.is_symlink():
        raise ValueError("refusing to replace a symbolic-link output")


@contextmanager
def _product(output: Path, files):
    protected = list(files)
    runtime = os.environ.get("PCC_RUNTIME_ARCHIVE")
    if runtime:
        protected.append(Path(runtime))
    _protect_output(output, protected)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".pcc-meson-") as directory:
        draft = Path(directory) / "product"
        yield draft
        if not draft.is_file() or not draft.stat().st_size:
            raise ValueError("PCC produced no output")
        _protect_output(output, protected)
        os.replace(draft, output)


def _target() -> str:
    from pcc.frontends.python.pipeline_targets import host_target_triple
    target = host_target_triple()
    if target != "x86_64-unknown-linux-gnu":
        raise ValueError("PCC Meson requires native Linux x86-64")
    return target


def archive(args) -> None:
    from pcc.backend.ar_writer import write_archive
    _target()
    if len(args) < 2 or args[0] != "--create" or any(x.startswith("-") for x in args[1:]):
        raise ValueError("PCC ar requires --create OUTPUT [OBJECT ...]")
    output = Path(args[1]); objects = [Path(name) for name in args[2:]]
    with _product(output, objects) as draft:
        members = []
        for path in objects:
            data = path.read_bytes()
            # The writer performs the complete ET_REL validation and builds its
            # symbol index from definitions, not a supplied name/sidecar list.
            if (data[:7] != b"\x7fELF\x02\x01\x01" or data[18:20] != b"\x3e\x00"):
                raise ValueError(f"not a Linux x86-64 ELF object: {path}")
            members.append((path.name, data))
        draft.write_bytes(write_archive(members))


def parse(lang, args):
    if lang not in _SOURCE:
        raise ValueError("expected explicit c or cpp language")
    flags, inputs = [], []
    output, obj, level = None, False, 0
    operands = {"-I", "-D", "-U", "-isystem", "-include"}
    dialect = "-std=" + ("c11" if lang == "c" else "c++17")
    sequence = iter(args)
    last_category = 0
    for arg in sequence:
        if arg == "-c":
            if obj:
                raise ValueError("duplicate -c")
            obj = True
        elif arg == "-o" or arg in operands:
            value = next(sequence, None)
            if not value:
                raise ValueError("missing operand for " + arg)
            if arg == "-o":
                if output is not None:
                    raise ValueError("duplicate -o")
                output = Path(value)
            else:
                flags.extend((arg, value))
        elif arg.startswith(("-I", "-D", "-U")) or arg in ("-nostdinc", dialect):
            flags.append(arg)
        elif arg in ("-O0", "-O1", "-O2", "-O3"):
            level = int(arg[-1])
        elif arg.startswith("-"):
            raise ValueError(f"unsupported PCC {lang} option: {arg}")
        else:
            path = Path(arg)
            if path.suffix in _SOURCE[lang]:
                category = 0
            elif path.suffix in (".o", ".a"):
                category = 1 if path.suffix == ".o" else 2
            else:
                raise ValueError(f"unsupported PCC {lang} input: {arg}")
            if category < last_category:
                raise ValueError("PCC requires sources, then objects, then archives in their original order")
            last_category = category
            inputs.append(path)
    if not inputs:
        raise ValueError("no compiler inputs")
    if obj and (len(inputs) != 1 or inputs[0].suffix not in _SOURCE[lang]):
        raise ValueError("compile-only requires one source of the selected language")
    return flags, inputs, output or (inputs[0].with_suffix(".o") if obj else Path("a.out")), obj, level


@contextmanager
def _owned_selection():
    # In particular, PCC_SELF_OBJ can explicitly select a host assembler in the
    # general-purpose frontend. This adapter must reject, not inherit or mask,
    # an incompatible selection, including invocations made outside Meson.
    expected = {"PCC_SELF_OBJ": "pcc", "PCC_SELF_LINK": "pcc", "PCC_IR_TO_OBJ_EMITTER": "pcc",
                "PCC_RUNTIME_BUILD": "owned", "PCC_RUNTIME_CC": "pcc", "PCC_RUNTIME_HIGH": "py"}
    root = Path(__file__).resolve().parents[2]
    for key, target in (("PCC_SOURCE_ROOT", root), ("PCC_REPO_ROOT", root), ("PCC_RUNTIME_DIR", root / "pcc/runtime")):
        if key in os.environ and Path(os.environ[key]).resolve() != target:
            raise ValueError(f"PCC Meson refuses alternate {key} source identity")
    for key, wanted in expected.items():
        if key in os.environ and os.environ[key] != wanted:
            raise ValueError(f"PCC Meson requires {key}={wanted}")
    added = [key for key in expected if key not in os.environ]
    try:
        for key in added:
            os.environ[key] = expected[key]
        yield
    finally:
        for key in added:
            del os.environ[key]


def run(tool, args):
    with _owned_selection():
        return _run(tool, args)


def _run(tool, args):
    if list(args) == ["--version"]:
        print(banner(tool)); return 0
    if tool == "ar":
        archive(args); return 0
    flags, inputs, output, obj, level = parse(tool, args)
    from pcc.driver.project import _collect_input_units
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    target = _target()
    ev = CEvaluator(backend="self", target_triple=target)
    runtime = None
    if not obj:
        if not os.environ.get("PCC_RUNTIME_ARCHIVE"):
            raise ValueError("PCC Meson linking requires a current explicit PCC_RUNTIME_ARCHIVE")
        runtime = ev.validate_owned_freestanding_request()
    with _product(output, inputs) as draft:
        units, objects, archives = [], [], []
        for path in inputs:
            if not path.is_file():
                raise ValueError("missing compiler input: " + str(path))
            if path.suffix == ".o":
                objects.append(str(path))
            elif path.suffix == ".a":
                archives.append(str(path))
            else:
                # The public source loader preserves the requested spelling of
                # symlinked TUs for relative includes and uses surrogateescape.
                selected, _ = _collect_input_units(os.path.abspath(path))
                units.extend(selected)
        if units:
            compiled = ev.compile_translation_units(units, use_system_cpp=False, use_compile_cache=False,
                                                      cpp_args=flags, frontend_opt_level=level)
            if obj:
                ev.emit_compiled_units(compiled, emit_obj=str(draft), optimize=level)
            else:
                ev.emit_executable(compiled, str(draft), optimize=level, link_args=objects + archives,
                                   freestanding_libc=True)
        else:
            from pcc.backend.owned_elf_link import link_inputs
            link_inputs(output=str(draft), target=target, objects=objects, archives=[*archives, str(runtime)])
    return 0


def main():
    try:
        if len(sys.argv) < 2:
            raise ValueError("missing explicit compiler language or ar")
        return run(sys.argv[1], sys.argv[2:])
    except Exception as err:
        print(f"pcc: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
