"""Public C-frontend coverage for the shared pcc-Python libc link mode."""

from __future__ import annotations

import ast
import hashlib
import json
import platform
import re
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from pcc.driver.cli_core import cli_main
from pcc.driver.project import collect_translation_units
from pcc.frontends.c.evaluator.c_evaluator import CEvaluator, FREESTANDING_C_LIBC_PY_MODULES


@pytest.mark.parametrize(
    "link_args",
    [
        ["-pie"],
        ["-static-pie"],
        ["-Wl,-pie"],
        ["-Wl,--pic-executable"],
        ["-Wl,-static,-pie"],
        ["-Xlinker", "-pie"],
        ["-Xlinker=--pic-executable"],
        ["-shared"],
        ["-Wl,--shared"],
        ["-Wl,-Bshareable"],
        ["-Xlinker", "--relocatable"],
    ],
)
def test_linux_freestanding_libc_rejects_pie_link_args_before_link_setup(
    monkeypatch, link_args
):
    evaluator = CEvaluator(target_triple="x86_64-unknown-linux-gnu")

    def unexpected_link_setup(*_args, **_kwargs):
        raise AssertionError("PIE link args must fail before freestanding setup")

    monkeypatch.setattr(evaluator, "_freestanding_link_inputs", unexpected_link_setup)

    with pytest.raises(RuntimeError, match="fixed ET_EXEC.*PIE/static-PIE"):
        evaluator.run_compiled_translation_units_with_system_cc(
            [],
            optimize=False,
            link_args=link_args,
            freestanding_libc=True,
        )


def test_c_cli_linux_freestanding_libc_rejects_pie_before_compilation(
    tmp_path, monkeypatch, capsys
):
    source = tmp_path / "main.c"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    monkeypatch.delenv("PCC_RUNTIME_CC", raising=False)
    monkeypatch.delenv("PCC_RUNTIME_HIGH", raising=False)

    def unexpected_compile(*_args, **_kwargs):
        raise AssertionError("PIE link args must fail before C compilation")

    monkeypatch.setattr(CEvaluator, "compile_translation_units", unexpected_compile)

    result = cli_main(
        [
            "--target=x86_64-unknown-linux-gnu",
            "--freestanding-libc",
            "--link-arg=-pie",
            str(source),
        ]
    )

    assert result == 1
    error = capsys.readouterr().err
    assert "fixed ET_EXEC" in error
    assert "PIE/static-PIE" in error
    assert "-pie" in error


def test_linux_freestanding_libc_forwards_non_pie_link_args(
    monkeypatch,
):
    evaluator = CEvaluator(target_triple="x86_64-unknown-linux-gnu")
    link_calls = []

    monkeypatch.setattr(
        evaluator,
        "_freestanding_link_inputs",
        lambda _tmpdir, *, enabled, timeout: (
            ["start.s"],
            ["-nostdlib", "-static", "-no-pie"],
            ["runtime.a"],
        ),
    )

    def fake_run(command, **_kwargs):
        link_calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("pcc.frontends.c.evaluator.c_evaluator.subprocess.run", fake_run)
    allowed = ["-Wl,-Map,freestanding.map", "-no-pie", "./libpie-helper.a"]

    result = evaluator.run_compiled_translation_units_with_system_cc(
        [],
        optimize=False,
        link_args=allowed,
        freestanding_libc=True,
    )

    assert result.returncode == 0
    assert link_calls[0][-4:-1] == allowed


def test_c_cli_freestanding_libc_runs_pcc_python_mem_and_allocator(tmp_path):
    source = tmp_path / "freestanding_consumer.c"
    source.write_text(
        "typedef unsigned long size_t;\n"
        "void *malloc(size_t);\n"
        "void free(void *);\n"
        "void *memcpy(void *, const void *, size_t);\n"
        "size_t strlen(const char *);\n"
        "int main(void) {\n"
        "    const char *source = \"pcc-python\";\n"
        "    char *copy = (char *)malloc(16);\n"
        "    if (copy == (void *)0) return 10;\n"
        "    memcpy(copy, source, 11);\n"
        "    int ok = strlen(copy) == 10 && copy[4] == 'p';\n"
        "    free(copy);\n"
        "    return ok ? 0 : 11;\n"
        "}\n",
        encoding="utf-8",
    )

    result = cli_main(["--backend=self", "--freestanding-libc", str(source)])

    assert result == 0


def test_c_cli_freestanding_libc_link_map_selects_only_pcc_python_libc(
    tmp_path,
):
    source = tmp_path / "link_map_consumer.c"
    link_map = tmp_path / "freestanding.map"
    source.write_text(
        "typedef unsigned long size_t;\n"
        "void *malloc(size_t);\n"
        "void free(void *);\n"
        "size_t strlen(const char *);\n"
        "int main(void) {\n"
        "    char *value = (char *)malloc(8);\n"
        "    if (value == (void *)0) return 20;\n"
        "    value[0] = 'o'; value[1] = 'k'; value[2] = 0;\n"
        "    int ok = strlen(value) == 2;\n"
        "    free(value);\n"
        "    return ok ? 0 : 21;\n"
        "}\n",
        encoding="utf-8",
    )

    if sys.platform == "darwin":
        link_map_arg = "--link-arg=-Wl,-map," + str(link_map)
    else:
        assert sys.platform.startswith("linux")
        assert platform.machine().lower() in {"x86_64", "amd64"}
        link_map_arg = "--link-arg=-Wl,-Map," + str(link_map)

    result = cli_main(
        ["--backend=self", "--freestanding-libc", link_map_arg, str(source)]
    )

    assert result == 0
    ownership = link_map.read_text(encoding="utf-8")
    assert "vendor_" not in ownership
    if sys.platform == "darwin":
        assert "libpy_runtime_pcc_py.a(freestanding_allocator.o)" in ownership
        assert "libpy_runtime_pcc_py.a(freestanding_mem_str.o)" in ownership
        declared = [
            json.loads(line[len("# declared_library "):])
            for line in ownership.splitlines()
            if line.startswith("# declared_library ")
        ]
        assert declared == [
            {"ordinal": 1, "install_name": "/usr/lib/libSystem.B.dylib"}
        ]
        imports = [
            json.loads(line[len("# dynamic_import "):])
            for line in ownership.splitlines()
            if line.startswith("# dynamic_import ")
        ]
        # The owned image declares libSystem as the import provider. It does
        # not consume SDK .tbd files or establish physical reexport owners.
        assert {item["symbol"] for item in imports} == {"_mmap", "_munmap"}
        assert all(item["library_ordinal"] == 1 and item["weak"] is False
                   for item in imports)
        assert ".tbd" not in ownership
    else:
        assert "libpy_runtime_pcc_py.a(freestanding_allocator.o)" in ownership
        assert "libpy_runtime_pcc_py.a(freestanding_mem_str.o)" in ownership
        assert "libc.a" not in ownership
    # Attribute the bytes actually selected by the owned linker to the
    # admitted production archive, without manufacturing a C-only alias.
    for member in ("freestanding_allocator.o", "freestanding_mem_str.o"):
        selected = re.search(
            r"^(.+libpy_runtime_pcc_py\.a)\(" + re.escape(member)
            + r"\) archive_sha256=([0-9a-f]{64}) member_sha256=([0-9a-f]{64})$",
            ownership,
            re.MULTILINE,
        )
        assert selected is not None, member
        archive_bytes = Path(selected.group(1)).read_bytes()
        assert hashlib.sha256(archive_bytes).hexdigest() == selected.group(2)
        if sys.platform == "darwin":
            from pcc.backend.macho_archive import read_archive

            member_bytes = {item.name: item.data for item in read_archive(archive_bytes)}[member]
        else:
            from pcc.backend.elf_x86_64 import read_archive_payloads

            member_bytes = dict(read_archive_payloads(archive_bytes))[member]
        assert hashlib.sha256(member_bytes).hexdigest() == selected.group(3)


def test_c_freestanding_libc_link_view_lists_only_pcc_python_sources():
    runtime_py = Path(__file__).resolve().parents[2] / "pcc" / "runtime" / "py"
    assert FREESTANDING_C_LIBC_PY_MODULES == (
        "freestanding_mem_str",
        "freestanding_allocator",
        "freestanding_platform_io",
        "freestanding_platform_fs",
        "freestanding_platform_env",
        "freestanding_platform_system",
        "freestanding_platform_time",
        "freestanding_platform_process",
        "freestanding_platform_socket",
        "freestanding_stdio",
        "freestanding_linux_libc",
        "freestanding_errno",
        "freestanding_gc_state",
        "freestanding_runtime_traps",
    )
    for module_name in FREESTANDING_C_LIBC_PY_MODULES:
        source = runtime_py / (module_name + ".py")
        assert source.is_file(), module_name
        assert "__pcc_freestanding__ = True" in source.read_text(encoding="utf-8")
        assert not source.with_suffix(".c").exists(), module_name
        assert not source.with_suffix(".s").exists(), module_name


def test_linux_freestanding_c_archive_closes_explicit_symbol_dependencies():
    runtime_py = Path(__file__).resolve().parents[2] / "pcc" / "runtime" / "py"
    exports = {}
    externs = set()
    globals_ = {}
    global_refs = set()
    modules = FREESTANDING_C_LIBC_PY_MODULES + ("freestanding_c_linux_start",)
    for module_name in modules:
        tree = ast.parse((runtime_py / (module_name + ".py")).read_text("utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id in {"c_abi_export", "c_abi_typed_export"}:
                symbol = ast.literal_eval(node.args[0])
                assert symbol not in exports, (symbol, exports.get(symbol), module_name)
                exports[symbol] = module_name
            elif node.func.id == "extern":
                externs.add(ast.literal_eval(node.args[0]))
            elif node.func.id.startswith(("define_global_", "define_thread_local_")):
                symbol = ast.literal_eval(node.args[0])
                assert symbol not in globals_, (symbol, globals_.get(symbol), module_name)
                globals_[symbol] = module_name
            elif node.func.id in {"global_addr", "global_load_ptr", "global_store_ptr"}:
                global_refs.add(ast.literal_eval(node.args[0]))
    # The C translation unit supplies main; the private archive must supply
    # every explicit runtime extern, including startup exit and libc errno.
    assert externs - exports.keys() == {"main"}
    assert exports["lseek"] == "freestanding_linux_libc"
    assert exports["pcc_errno_set"] == "freestanding_errno"
    assert exports.keys().isdisjoint(globals_)
    # GNU ld supplies these bounds. All other global references must resolve
    # inside this standalone archive, not through the separate full runtime.
    assert global_refs - globals_.keys() == {
        "__init_array_start", "__init_array_end",
        "__fini_array_start", "__fini_array_end",
    }
    assert globals_["pcc_errno_storage"] == "freestanding_errno"
    assert globals_["pcc_gc_refcount_provenance_probe"] == "freestanding_gc_state"


def test_null_callee_trap_has_one_freestanding_runtime_owner():
    from pcc.frontends.python.owned_runtime_build import runtime_modules

    runtime_root = Path(__file__).resolve().parents[2] / "pcc" / "runtime"
    modules = set()
    for target in (
        "x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu",
        "arm64-apple-darwin", "x86_64-pc-windows-msvc",
    ):
        inventory = runtime_modules(str(runtime_root), target)
        assert inventory.count("freestanding_runtime_traps") == 1
        modules.update(inventory)
    owners = []
    for module_name in sorted(modules):
        source = (runtime_root / "py" / (module_name + ".py")).read_text("utf-8")
        if "pcc_null_callee_trap" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"c_abi_export", "c_abi_typed_export"}
                and ast.literal_eval(node.args[0]) == "pcc_null_callee_trap"
            ):
                owners.append(module_name)
    assert owners == ["freestanding_runtime_traps"]


def _assert_linux_standalone_archive_closure(archive_path, required_symbols):
    from pcc.backend.elf_x86_64 import read_archive, select_archive_members

    members = read_archive(Path(archive_path).read_bytes())
    selected = []
    _objects, unresolved = select_archive_members(
        members,
        required_symbols | {"pcc_platform_env_init", "exit"},
        already_defined={
            "_GLOBAL_OFFSET_TABLE_", "__init_array_start", "__init_array_end",
            "__fini_array_start", "__fini_array_end",
        },
        selected_names=selected,
    )
    # Inspect actual emitted references: the backend can insert a runtime
    # symbol (for example the null-callee trap) absent from source externs.
    assert not unresolved, sorted(unresolved)
    assert "freestanding_runtime_traps.o" in selected


def _check_linux_standalone_link_inputs(monkeypatch, evaluator, required_symbols):
    original = evaluator._freestanding_link_inputs

    def checked(tmpdir, *, enabled, timeout):
        result = original(tmpdir, enabled=enabled, timeout=timeout)
        assert enabled
        assert len(result[2]) == 1
        _assert_linux_standalone_archive_closure(result[2][0], required_symbols)
        return result

    monkeypatch.setattr(evaluator, "_freestanding_link_inputs", checked)


@pytest.mark.pcc_gate(
    unavailable=None
    if sys.platform.startswith("linux")
    and platform.machine().lower() in {"x86_64", "amd64"}
    else "the standalone system-cc freestanding archive requires Linux x86_64"
)
def test_linux_freestanding_system_link_runs_lseek_and_errno(tmp_path, monkeypatch):
    source = tmp_path / "seek_consumer.c"
    output = tmp_path / "seek_consumer.bin"
    link_map = tmp_path / "seek_consumer.map"
    source.write_text(
        r"""
int open(const char *, int, ...);
int close(int);
long lseek(int, long, int);
int *__errno_location(void);

int main(int argc, char **argv) {
    if (argc != 2) return 30;
    int *error = __errno_location();
    if (*error != 0) return 31;
    *error = 123;
    int fd = open(argv[1], 66, 0600);
    if (fd < 0 || *error != 123) return 32;
    long large = 4294967296L + 19;
    if (lseek(fd, large, 0) != large) return 33;
    if (lseek(fd, -7, 1) != large - 7) return 34;
    if (lseek(fd, 0, 1) != large - 7 || *error != 123) return 35;
    if (lseek(-1, 0, 0) != -1 || *error != 9) return 36;
    if (__errno_location() != error) return 37;
    if (lseek(fd, 0, 0) != 0 || *error != 9) return 38;
    if (close(fd) != 0 || *error != 9) return 39;
    return 0;
}
""",
        encoding="utf-8",
    )
    units, base_dir = collect_translation_units(str(source))
    evaluator = CEvaluator(backend="self", target_triple="x86_64-unknown-linux-gnu")
    compiled_units = evaluator.compile_translation_units(units, base_dir=base_dir)
    _check_linux_standalone_link_inputs(
        monkeypatch, evaluator, {"__errno_location", "open", "close", "lseek"},
    )
    # Exercise the exact private-archive route used by the zlib oracle, not
    # the separately built main runtime used by the owned-link CLI tests.
    result = evaluator.run_compiled_translation_units_with_system_cc(
        compiled_units,
        base_dir=base_dir,
        prog_args=[str(output)],
        link_args=["-Wl,-Map," + str(link_map)],
        freestanding_libc=True,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert output.stat().st_size == 0  # Seeking past 4 GiB writes no file data.
    ownership = link_map.read_text(encoding="utf-8")
    for module_name in (
        "freestanding_linux_libc", "freestanding_errno", "freestanding_gc_state",
        "freestanding_runtime_traps",
    ):
        assert "libpcc_freestanding_c.a(" + module_name + ".o)" in ownership
    assert "libpy_runtime_pcc_py.a" not in ownership
    assert "libc.a" not in ownership


@pytest.mark.pcc_gate(
    unavailable=None
    if sys.platform.startswith("linux")
    and platform.machine().lower() in {"x86_64", "amd64"}
    else "the standalone system-cc freestanding archive requires Linux x86_64"
)
def test_linux_freestanding_system_link_preserves_null_trap_fatal_contract(
    tmp_path, monkeypatch,
):
    source = tmp_path / "null_trap_consumer.c"
    source.write_text(
        "void pcc_null_callee_trap(void);\n"
        "int main(void) { pcc_null_callee_trap(); return 77; }\n",
        encoding="utf-8",
    )
    units, base_dir = collect_translation_units(str(source))
    evaluator = CEvaluator(backend="self", target_triple="x86_64-unknown-linux-gnu")
    compiled_units = evaluator.compile_translation_units(units, base_dir=base_dir)
    _check_linux_standalone_link_inputs(
        monkeypatch, evaluator, {"pcc_null_callee_trap"},
    )
    result = evaluator.run_compiled_translation_units_with_system_cc(
        compiled_units, base_dir=base_dir, freestanding_libc=True,
    )
    # The existing platform abort raises SIGABRT, with exit(134) as its
    # fallback. A normal return or a different failure is not this contract.
    assert result.returncode in (-signal.SIGABRT, 134), (result.stdout, result.stderr)
    assert result.stdout == ""
    assert result.stderr == "[NULL_CALL]\n"


def test_c_cli_freestanding_libc_runs_representative_project_consumers(tmp_path):
    sqlite_path = tmp_path / "sqlite-style.bin"
    consumers = {
        "lua": r"""
typedef unsigned long size_t;
void *calloc(size_t, size_t);
void *realloc(void *, size_t);
void free(void *);
void *memcpy(void *, const void *, size_t);
void *memmove(void *, const void *, size_t);
int strcmp(const char *, const char *);

int main(void) {
    char *buffer = (char *)calloc(8, 1);
    if (buffer == (void *)0) return 40;
    memcpy(buffer, "lua", 4);
    buffer = (char *)realloc(buffer, 16);
    if (buffer == (void *)0) return 41;
    memmove(buffer + 4, buffer, 4);
    int ok = strcmp(buffer, "lua") == 0 && strcmp(buffer + 4, "lua") == 0;
    free(buffer);
    return ok ? 0 : 42;
}
""",
        "sqlite": r"""
typedef unsigned long size_t;
typedef void FILE;
int snprintf(char *, size_t, const char *, ...);
FILE *fopen(const char *, const char *);
size_t fwrite(const void *, size_t, size_t, FILE *);
size_t fread(void *, size_t, size_t, FILE *);
int fclose(FILE *);
int remove(const char *);

int main(void) {
    const char *path = __PCC_SQLITE_PATH__;
    char row[16];
    if (snprintf(row, sizeof(row), "%s:%d", "row", 7) != 5) return 50;
    FILE *stream = fopen(path, "wb");
    if (stream == (void *)0) return 51;
    if (fwrite(row, 1, 5, stream) != 5 || fclose(stream) != 0) return 52;
    stream = fopen(path, "rb");
    if (stream == (void *)0) return 53;
    char copy[8] = {0};
    int ok = fread(copy, 1, 5, stream) == 5 && copy[3] == ':' && copy[4] == '7';
    if (fclose(stream) != 0 || remove(path) != 0) return 54;
    return ok ? 0 : 55;
}
""".replace("__PCC_SQLITE_PATH__", json.dumps(str(sqlite_path))),
        "zlib": r"""
typedef unsigned long size_t;
void *memset(void *, int, size_t);
void *memcpy(void *, const void *, size_t);
void *memmove(void *, const void *, size_t);
int memcmp(const void *, const void *, size_t);

int main(void) {
    unsigned char window[32];
    unsigned char expected[8] = {1, 2, 3, 4, 1, 2, 3, 4};
    unsigned char seed[4] = {1, 2, 3, 4};
    memset(window, 0, sizeof(window));
    memcpy(window, seed, sizeof(seed));
    memmove(window + 4, window, sizeof(seed));
    return memcmp(window, expected, sizeof(expected)) == 0 ? 0 : 60;
}
""",
    }

    for name, text in consumers.items():
        source = tmp_path / (name + "_style.c")
        source.write_text(text, encoding="utf-8")
        result = cli_main(["--backend=self", "--freestanding-libc", str(source)])
        assert result == 0, name


def test_c_cli_freestanding_libc_rejects_non_pcc_python_runtime(
    tmp_path, monkeypatch, capsys
):
    source = tmp_path / "main.c"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    foreign = tmp_path / "foreign_runtime.a"
    foreign.write_bytes(b"!<arch>\n")
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(foreign))

    result = cli_main(["--freestanding-libc", str(source)])

    assert result == 1
    assert "requires libpy_runtime_pcc_py.a" in capsys.readouterr().err


def test_c_cli_freestanding_libc_rejects_python_and_emit_only(
    tmp_path, capsys
):
    python_source = tmp_path / "main.py"
    python_source.write_text("print('no')\n", encoding="utf-8")
    c_source = tmp_path / "main.c"
    c_source.write_text("int main(void) { return 0; }\n", encoding="utf-8")

    assert cli_main(["--freestanding-libc", str(python_source)]) == 1
    assert cli_main(
        [
            "--freestanding-libc",
            "--emit-obj",
            str(tmp_path / "main.o"),
            str(c_source),
        ]
    ) == 1

    errors = capsys.readouterr().err
    assert "only valid for C inputs" in errors
    assert "requires a final link/run" in errors
