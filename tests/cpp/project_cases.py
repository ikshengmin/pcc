"""Pinned smoke/upstream fixtures; reference and native owners stay separate."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from tests.cpp.owned_execution import Execution, assert_exact

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "projects/tinyxml2-11.0.0"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class ProjectExpectation:
    returncode: int
    stderr: bytes | None
    stdout: bytes | None = None
    stdout_regex: bytes | None = None
    stdout_groups: tuple[tuple[str, bytes], ...] = ()


def _project_path(directory: Path, name: str) -> Path:
    # Validate before Path can normalize away traversal/repeated separators.
    # Reject Windows drive/alternate-stream spellings on every host as well.
    if (
        not isinstance(name, str)
        or "\\" in name
        or ":" in name
        or "\0" in name
        or any(part in ("", ".", "..") for part in name.split("/"))
    ):
        raise ValueError("project inputs need canonical relative paths")
    path = directory / name
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError("project fixture symlink escapes its directory")
    return path


def _digest(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("project input needs a lowercase SHA256 digest")
    return value


@dataclass(frozen=True)
class ProjectFixture:
    name: str
    revision: str
    standard: str
    variant: str
    directory: Path
    manifest_sha256: str
    input_sha256: tuple[tuple[str, str], ...]
    files: tuple[Path, ...]
    translation_units: tuple[str, ...]
    driver: Path | None
    driver_sha256: str | None
    preprocessor_definitions: tuple[str, ...]
    expected: ProjectExpectation

    def verify(self) -> None:
        assert sha256(self.directory / "pcc-fixture.json") == self.manifest_sha256, (
            "project manifest changed after selection"
        )
        for name, digest in self.input_sha256:
            path = _project_path(self.directory, name)
            assert path.is_file(), f"missing project input: {path}"
            assert sha256(path) == digest, f"project input SHA256 mismatch: {path}"
        if self.driver is not None:
            if not self.driver.resolve().is_relative_to(ROOT):
                raise ValueError("project driver escapes the repository")
            assert self.driver.is_file(), f"missing project input: {self.driver}"
            assert sha256(self.driver) == self.driver_sha256, (
                f"project driver SHA256 mismatch: {self.driver}"
            )


def load_project(
    directory: Path = PROJECT, *, variant: str = "smoke"
) -> ProjectFixture:
    manifest = directory / "pcc-fixture.json"
    configuration = json.loads(manifest.read_text(encoding="utf-8"))
    if configuration["schema"] != "pcc.cpp-project-fixture.v1":
        raise ValueError("unsupported project fixture schema")
    selection = configuration["fixtures"][variant]
    units = tuple(selection["translation_units"])
    names = tuple(selection["inputs"])
    if (
        not units
        or len(set(units)) != len(units)
        or len(set(names)) != len(names)
        or not set(units).issubset(names)
        or configuration["license_file"] not in names
    ):
        raise ValueError("project needs distinct inputs including its TUs and license")
    files = tuple(_project_path(directory, name) for name in names)
    digests = configuration["sha256"]
    if not set(names).issubset(digests):
        raise ValueError("project input lacks a SHA256 pin")
    _digest(configuration["source_archive"]["sha256"])
    driver_name = selection.get("driver")
    driver = _project_path(ROOT, driver_name) if driver_name is not None else None
    if driver is not None and "pcc_test_main.cpp" in names:
        raise ValueError("project input collides with the smoke driver")
    expected = selection["expected"]
    if ("stdout" in expected) == ("stdout_regex" in expected):
        raise ValueError("project expectation needs exactly one stdout matcher")
    if "stdout" in expected and "stderr" not in expected:
        raise ValueError("exact project expectation needs stdout and stderr")
    pattern = expected.get("stdout_regex")
    groups = tuple(
        (name, value.encode("ascii"))
        for name, value in expected.get("stdout_groups", {}).items()
    )
    if pattern is not None:
        compiled = re.compile(pattern.encode("utf-8"))
        if set(dict(groups)) != set(compiled.groupindex):
            raise ValueError("project stdout captures need explicit group expectations")
        for _name, group_pattern in groups:
            re.compile(group_pattern)
    elif groups:
        raise ValueError("exact project stdout cannot have group expectations")
    fixture = ProjectFixture(
        configuration["name"],
        configuration["revision"],
        configuration["standard"],
        variant,
        directory,
        sha256(manifest),
        tuple((name, _digest(digests[name])) for name in names),
        files,
        units,
        driver,
        _digest(selection["driver_sha256"]) if driver is not None else None,
        tuple(selection.get("preprocessor_definitions", ())),
        ProjectExpectation(
            expected["returncode"],
            expected["stderr"].encode("utf-8") if "stderr" in expected else None,
            expected["stdout"].encode("utf-8") if "stdout" in expected else None,
            pattern.encode("utf-8") if pattern is not None else None,
            groups,
        ),
    )
    fixture.verify()
    return fixture


def materialize_project(fixture: ProjectFixture, directory: Path) -> tuple[Path, ...]:
    fixture.verify()
    directory.mkdir()
    for path, (name, _digest_value) in zip(fixture.files, fixture.input_sha256):
        destination = directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(path.read_bytes())
    units = tuple(directory / name for name in fixture.translation_units)
    if fixture.driver is not None:
        driver = directory / "pcc_test_main.cpp"
        driver.write_bytes(fixture.driver.read_bytes())
        units += (driver,)
    fixture.verify()
    verify_materialized_project(fixture, directory)
    return units


def verify_materialized_project(fixture: ProjectFixture, directory: Path) -> None:
    """Check copied inputs, not newly generated outputs such as resources/out/*.xml.

    TinyXML2 never intentionally rewrites a pinned input. A future project with
    mutable inputs needs an explicit contract, not silently weakened hashing.
    """
    inputs = fixture.input_sha256
    if fixture.driver is not None:
        inputs += (("pcc_test_main.cpp", fixture.driver_sha256),)
    for name, digest in inputs:
        path = _project_path(directory, name)
        assert path.is_file(), f"missing materialized project input: {path}"
        assert sha256(path) == digest, (
            f"materialized project input SHA256 mismatch: {path}"
        )


def assert_project_outcome(
    actual: Execution, fixture: ProjectFixture
) -> dict[str, str]:
    """Keep raw output; upstream timing lines are not an exact-output contract."""
    expected = fixture.expected
    if expected.stdout is not None:
        assert expected.stderr is not None
        assert_exact(
            actual, Execution(expected.returncode, expected.stdout, expected.stderr)
        )
        return {"matcher": "exact"}
    assert actual.returncode == expected.returncode, (
        f"project returncode mismatch: {actual.returncode} != {expected.returncode}"
    )
    if expected.stderr is not None:
        assert actual.stderr == expected.stderr, (
            f"project stderr mismatch: {actual.stderr!r}"
        )
    assert expected.stdout_regex is not None
    matches = list(re.finditer(expected.stdout_regex, actual.stdout))
    assert len(matches) == 1, (
        "project requires exactly one upstream summary; "
        f"pattern={expected.stdout_regex!r}, matches={len(matches)}"
    )
    for name, pattern in expected.stdout_groups:
        value = matches[0].group(name)
        assert value is not None and re.fullmatch(pattern, value), (
            f"project summary {name} mismatch: {value!r} does not match {pattern!r}"
        )
    return {
        "matcher": "upstream-summary",
        **{
            name: value.decode("ascii")
            for name, value in matches[0].groupdict().items()
        },
    }


def _fixture_receipt(fixture: ProjectFixture) -> dict:
    return {
        "project": fixture.name,
        "variant": fixture.variant,
        "revision": fixture.revision,
        "standard": fixture.standard,
        "manifest_sha256": fixture.manifest_sha256,
        "input_sha256": dict(fixture.input_sha256),
        "driver_sha256": fixture.driver_sha256,
    }


def run_project(
    fixture: ProjectFixture, binary: Path, root: Path, *, owner: str
) -> Execution:
    """Run the unchanged main with no arguments in a fresh writable source copy.

    This matches upstream xmltest's CTest cwd/argv, not CMake build/install
    qualification. It cannot select xmltest's alternate single-file mode.
    Each call retains raw streams and a failure/timeout-aware receipt.
    """
    if owner not in ("external-reference", "native-pcc1"):
        raise ValueError("project execution owner must be explicit")
    root.mkdir()
    materialize_project(fixture, root / "source")
    binary = binary.resolve(strict=True)
    command = [str(binary)]
    receipt = {
        **_fixture_receipt(fixture),
        "owner": owner,
        "command": command,
        "cwd": str(root / "source"),
        "binary_sha256": sha256(binary),
        "status": "run-not-completed",
    }
    try:
        result = subprocess.run(
            command, cwd=root / "source", capture_output=True, text=False, timeout=60
        )
        (root / "stdout").write_bytes(result.stdout)
        (root / "stderr").write_bytes(result.stderr)
        actual = Execution(result.returncode, result.stdout, result.stderr)
        receipt["returncode"] = result.returncode
        receipt["status"] = "executed-not-accepted"
        receipt["outcome"] = assert_project_outcome(actual, fixture)
        receipt["status"] = "passed-" + fixture.variant
    except subprocess.TimeoutExpired as error:
        (root / "stdout").write_bytes(error.stdout or b"")
        (root / "stderr").write_bytes(error.stderr or b"")
        receipt["status"] = "timeout"
        receipt["error"] = repr(error)
        raise
    except Exception as error:
        receipt["error"] = repr(error)
        raise
    finally:
        try:
            receipt["binary_unchanged"] = (
                binary.is_file() and sha256(binary) == receipt["binary_sha256"]
            )
            assert receipt["binary_unchanged"], "project executable changed during run"
            fixture.verify()
            verify_materialized_project(fixture, root / "source")
        except Exception as error:
            receipt["status"] = "input-changed"
            receipt["integrity_error"] = repr(error)
            raise
        finally:
            (root / "execution.json").write_text(
                json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
            )
    return actual


def compile_project_oracle(
    fixture: ProjectFixture, root: Path, *, compiler: str
) -> Path:
    """Explicit external reference, never called by the owned/native adapter."""
    # Preserve driver spelling: clang++ may be a symlink to clang, whose argv[0]
    # selects different link defaults. Make it absolute before changing cwd,
    # but record/hash the resolved executable separately.
    compiler_invocation = Path(compiler).absolute()
    compiler_path = compiler_invocation.resolve(strict=True)
    units = materialize_project(fixture, root / "source")
    binary = root / "reference"
    command = [
        str(compiler_invocation),
        "-std=" + fixture.standard,
        "-pedantic-errors",
        "-O0",
        *("-D" + value for value in fixture.preprocessor_definitions),
        *(str(path) for path in units),
        "-o",
        str(binary),
    ]
    receipt = {
        **_fixture_receipt(fixture),
        "owner": "external-reference",
        "compiler": str(compiler_invocation),
        "compiler_resolved_target": str(compiler_path),
        "compiler_sha256": sha256(compiler_path),
        "command": command,
        "status": "compile-not-completed",
    }
    try:
        result = subprocess.run(
            command, cwd=root, capture_output=True, text=False, timeout=60
        )
        (root / "reference.stdout").write_bytes(result.stdout)
        (root / "reference.stderr").write_bytes(result.stderr)
        receipt["returncode"] = result.returncode
        assert result.returncode == 0, (
            f"external C++17 project oracle failed ({result.returncode}): "
            f"stdout={result.stdout!r}, stderr={result.stderr!r}"
        )
        assert binary.is_file(), (
            "external compiler returned success without an executable"
        )
        receipt["output_sha256"] = sha256(binary)
        receipt["status"] = "compiled-not-executed"
    except subprocess.TimeoutExpired as error:
        (root / "reference.stdout").write_bytes(error.stdout or b"")
        (root / "reference.stderr").write_bytes(error.stderr or b"")
        receipt["status"] = "timeout"
        receipt["error"] = repr(error)
        raise
    except Exception as error:
        receipt["error"] = repr(error)
        raise
    finally:
        try:
            receipt["compiler_unchanged"] = (
                compiler_invocation.is_file()
                and compiler_invocation.resolve() == compiler_path
                and sha256(compiler_invocation) == receipt["compiler_sha256"]
            )
            assert receipt["compiler_unchanged"], (
                "reference compiler changed during build"
            )
            fixture.verify()
            verify_materialized_project(fixture, root / "source")
        except Exception as error:
            receipt["status"] = "input-changed"
            receipt["integrity_error"] = repr(error)
            raise
        finally:
            (root / "reference-compile-receipt.json").write_text(
                json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
            )
    return binary


def native_project_environment(root: Path) -> dict[str, str]:
    """Do not inherit a host compiler route or rebuild an unspecified runtime."""
    environment = dict(os.environ)
    empty_path = root / "empty-path"
    empty_path.mkdir()
    forbidden = str(root / "forbidden-external-tool")
    environment.update(
        {
            "PATH": str(empty_path),
            "CC": forbidden,
            "CXX": forbidden,
            "PCC_HOST_PYTHON": forbidden,
            "PCC_HOST_PCC": forbidden,
            "PCC_RUNTIME_CC": "pcc",
            "PCC_RUNTIME_HIGH": "py",
            "PCC_SELF_LINK": "pcc",
            "PCC_SELF_OBJ": "pcc",
            "PCC_IR_TO_OBJ_EMITTER": "pcc",
            "PCC_NO_AUTO_PCC1": "1",
            "PCC_PY_FRONTEND_IR_CACHE": "0",
            "PCC_SELF_BACKEND_OBJECT_CACHE": "0",
            "PCC_DEFER_FRONTEND_CODEGEN": "0",
        }
    )
    return environment


def compile_project_native(compiler: Path, fixture: ProjectFixture, root: Path) -> Path:
    """Exercise the Linux/Windows directory/separate-TU public route used for C projects.

    No missing compiler/runtime/capability is converted to skip or to an oracle
    invocation. C++17 is the intended default C++ language contract. This gate
    remains unsatisfied until the public native C++ directory route exists.
    """
    from scripts.bootstrap_platform import dependency_receipt
    from scripts.platform_process_watchdog import run
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive

    assert compiler.is_file(), f"native compiler missing: {compiler}"
    assert compiler.read_bytes().startswith(
        (b"\x7fELF", b"MZ")
    ), "native compiler must be an executable artifact, not a shell/Python wrapper"
    # A shell/Python wrapper or dynamically Python/LLVM-linked executable is not
    # accepted as the supplied native compiler. Static source ownership still
    # needs the compiler's build receipt; this header check alone is narrower.
    assert (
        sys.platform.startswith("linux") or sys.platform == "win32"
    ), "native C++ project adapter covers Linux/Windows ELF/PE only, not Darwin"
    compiler_shape = dependency_receipt(compiler)
    explicit = os.environ.get("PCC_RUNTIME_ARCHIVE", "")
    assert (
        explicit
    ), "provide a source-matching PCC_RUNTIME_ARCHIVE; no implicit rebuild"
    archive, _manifest = _verified_test_runtime_archive(explicit)
    materialize_project(fixture, root / "source")
    environment = native_project_environment(root)
    environment["PCC_RUNTIME_ARCHIVE"] = str(archive)
    output = root / "native-project"
    command = [
        str(compiler),
        "--backend",
        "self",
        "--separate-tus",
        "--no-cache",
        "-O0",
        "--cpp-arg=-std=" + fixture.standard,
        *("--cpp-arg=-D" + value for value in fixture.preprocessor_definitions),
        str(root / "source"),
        "-o",
        str(output),
    ]
    receipt = {
        **_fixture_receipt(fixture),
        "owner": "native-pcc1",
        "platform_scope": "linux-windows-elf-pe",
        "compiler": str(compiler),
        "compiler_sha256": sha256(compiler),
        "compiler_shape": compiler_shape,
        "runtime_sha256": sha256(archive),
        "command": command,
        "status": "compile-not-completed",
    }
    try:
        receipt["compile"] = run(
            command,
            cwd=root,
            env=environment,
            log_path=root / "native-compile.log",
            timeout=300,
            rss_limit=4 * 1024**3,
            native=True,
        )
        assert (
            output.is_file()
        ), "native C++ compiler returned success without an executable"
        receipt["output_shape"] = dependency_receipt(output)
        receipt["output_sha256"] = sha256(output)
        receipt["status"] = "compiled-not-executed"
    except Exception as error:
        receipt["error"] = repr(error)
        if isinstance(error, subprocess.CalledProcessError):
            receipt["returncode"] = error.returncode
        raise
    finally:
        try:
            receipt["compiler_unchanged"] = (
                compiler.is_file() and sha256(compiler) == receipt["compiler_sha256"]
            )
            receipt["runtime_unchanged"] = (
                archive.is_file() and sha256(archive) == receipt["runtime_sha256"]
            )
            assert receipt["compiler_unchanged"] and receipt["runtime_unchanged"]
            fixture.verify()
            verify_materialized_project(fixture, root / "source")
        except Exception as error:
            receipt["status"] = "input-changed"
            receipt["integrity_error"] = repr(error)
            raise
        finally:
            (root / "native-compile-receipt.json").write_text(
                json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
            )
    return output
