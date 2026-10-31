"""Actual raw-process differential, separate from historical value comparisons.

Original programs are reused without rewriting. A separate JDK observer writes
the same primitive little-endian bytes as the owned assembly observer. Neither
observer is Java production lowering, and javac is never a production dependency.

JLS21 15.25, 15.17.2-3, 14.11, 14.13 and 16:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-14.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-16.html
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

from pcc.frontends.java import compile_source
from tests.java import test_source_conditional as conditional
from tests.java import test_source_division as division
from tests.java import test_source_switch_do as control
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_scalars import NATIVE_REASON


JVM_LIMITS = ("-Xms16m", "-Xmx128m", "-XX:MaxMetaspaceSize=96m")
REFERENCE_REASON = (NATIVE_REASON or
                    (None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else
                     "set PCC_JAVA_RAW_REFERENCE to the selected dev-only JDK java executable"))


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1_048_576), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def tool_identity(java):
    home = java.parent.parent
    files = (java, home / "release", home / "lib/modules", home / "lib/server/libjvm.so")
    return {str(path): {"size": path.stat().st_size, "sha256": digest(path)} for path in files}


def record_process(command, directory, name, timeout=10):
    result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    (directory / (name + ".stdout.bin")).write_bytes(result.stdout)
    (directory / (name + ".stderr.bin")).write_bytes(result.stderr)
    (directory / (name + ".execution.json")).write_text(json.dumps({
        "command": list(command), "returncode": result.returncode,
        "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
    }, indent=2) + "\n")
    return result.returncode, result.stdout, result.stderr


def original_cases():
    lanes = {"int": "I", "long": "J", "boolean": "Z"}
    for name, result, body, helpers, expected in conditional.CASES:
        if name in ("unchosen-false-arm-must-not-run", "boolean-dynamic"):
            yield name, "Choice", conditional.original(result, body, helpers), lanes[result], (), expected
    for name, dtype, body, argument, expected in division.CASES:
        if name in ("long-no-float-rounding", "long-min-div-minus-one"):
            lane = lanes[dtype]
            yield name, "Division", division.original(dtype, body), lane, ((lane, argument),), expected
    for name, body, helpers, lane, expected in control.CASES:
        if name in ("switch-fallthrough", "do-continue-to-test"):
            result = {"I": "int", "J": "long", "Z": "boolean"}[lane]
            yield name, "Control", control.original(body, helpers, result), lane, (), expected


def observer_source(owner, lane, arguments):
    values = []
    for kind, value in arguments:
        if kind == "Z":
            values.append("true" if value else "false")
        elif kind in ("I", "J"):
            values.append(str(value) + ("L" if kind == "J" else ""))
        else:
            raise ValueError("unsupported observer argument lane")
    width = {"Z": 1, "I": 4, "J": 8}[lane]
    call = owner + ".run(" + ",".join(values) + ")"
    value = "(" + call + "?1L:0L)" if lane == "Z" else call
    return ("public final class PccRawObserve {public static void main(String[] args) {"
            "long value=" + value + ";"
            "for(int i=0;i<" + str(width) + ";i++){System.out.write((int)(value >>> (8*i)) & 255);}"
            "System.out.flush();}}")


@pytest.mark.parametrize("lane,width", (("Z", 1), ("I", 4), ("J", 8)))
def test_raw_reference_observer_calls_original_once_and_writes_declared_width(lane, width):
    text = observer_source("Original", lane, (("I", -7), ("J", -9223372036854775808)))
    assert text.count("Original.run(") == 1
    assert "-7,-9223372036854775808L" in text
    assert "i<" + str(width) in text
    assert "value >>> (8*i)" in text
    assert ("?1L:0L" in text) == (lane == "Z")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=REFERENCE_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
def test_original_sources_match_actual_native_and_jdk_stdout_stderr_exit(tmp_path, monkeypatch, request):
    java = Path(os.environ["PCC_JAVA_RAW_REFERENCE"]).resolve(strict=True)
    source_level = os.environ.get("PCC_JAVA_RAW_REFERENCE_SOURCE", "21")
    assert source_level in ("17", "21"), "select the reference source/target level explicitly"
    release = (java.parent.parent / "release").read_text()
    version = re.search(r'^JAVA_VERSION="([^"]+)"', release, re.MULTILINE)
    assert version, "selected JDK has no version metadata"
    assert int(version.group(1).split(".")[0]) >= int(source_level), (
        "selected JDK cannot compile the configured reference source level; "
        "a JDK17 reference must explicitly set PCC_JAVA_RAW_REFERENCE_SOURCE=17")
    before = tool_identity(java)
    originals = []
    observers = []
    inputs = tuple(Path(module.__file__) for module in (conditional, division, control)) + (Path(__file__),)
    source_before = {str(path): digest(path) for path in inputs}
    owner = {"reference_owner": "explicit existing JDK; development-only",
             "version": version.group(1), "source_level": source_level, "target_level": source_level,
             "platform_contract": "current selected runtime, not cross-release --release compatibility",
             "tools_before": before, "test_inputs_before": source_before,
             "production_dependency": False}
    (tmp_path / "owner-before.json").write_text(json.dumps(owner, indent=2) + "\n")

    def finish_identity():
        after = tool_identity(java)
        unchanged = all(path.read_bytes() == text.encode("utf-8") for path, text in originals + observers)
        source_after = {str(path): digest(path) for path in inputs}
        (tmp_path / "owner-post.json").write_text(json.dumps({
            **owner, "tools_after": after, "test_inputs_after": source_after,
            "original_and_observer_files": len(originals) + len(observers), "source_inputs_unchanged": unchanged,
        }, indent=2) + "\n")
        assert before == after and source_before == source_after and unchanged

    request.addfinalizer(finish_identity)
    version_result = record_process((str(java), *JVM_LIMITS, "--version"), tmp_path, "version")
    assert version_result[0] == 0
    modules = record_process((str(java), *JVM_LIMITS, "--list-modules"), tmp_path, "modules")
    assert modules[0] == 0 and any(line.split(b"@")[0] == b"jdk.compiler" for line in modules[1].splitlines())
    cases = tuple(original_cases())
    assert len(cases) == 6
    for name, class_name, text, lane, arguments, expected in cases:
        directory = tmp_path / name
        directory.mkdir()
        unit = directory / (class_name + ".java")
        unit.write_bytes(text.encode("utf-8"))
        originals.append((unit, text))
        native = directory / "native"
        native.mkdir()
        descriptor = "(" + "".join(kind for kind, value in arguments) + ")" + lane
        with monkeypatch.context() as denied:
            forbid_bytecode_or_external_owner(denied)
            compiled = compile_source(text, filename=unit.name, descriptor=descriptor)
        assert observe_scalar(compiled, native, monkeypatch, arguments) == expected
        # A fresh execution of the already emitted, identical native binary
        # retains all three streams; no Java arithmetic acts as its observer.
        actual = record_process((str(native / "observed"),), native, "raw-native")
        observer = directory / "PccRawObserve.java"
        observer_text = observer_source(class_name, lane, arguments)
        observer.write_text(observer_text, encoding="utf-8")
        observers.append((observer, observer_text))
        classes = directory / "classes"
        classes.mkdir()
        compiler = record_process((str(java), *JVM_LIMITS, "--module", "jdk.compiler/com.sun.tools.javac.Main",
                                   "-proc:none", "-encoding", "UTF-8", "-source", source_level, "-target", source_level,
                                   "-classpath", str(classes), "-d", str(classes), str(unit), str(observer)),
                                  directory, "reference-compile", 20)
        assert compiler[0] == 0, compiler
        reference = record_process((str(java), *JVM_LIMITS, "-cp", str(classes), "PccRawObserve"),
                                   directory, "reference-run")
        width = {"Z": 1, "I": 4, "J": 8}[lane]
        expected_bytes = int(expected).to_bytes(width, "little", signed=lane != "Z")
        (directory / "comparison.json").write_text(json.dumps({
            "case": name, "comparison": "raw_process_streams", "source_sha256": digest(unit),
            "original_modifiers_or_source_rewritten": False, "descriptor": descriptor, "arguments": arguments,
            "observer_sha256": digest(observer), "object_sha256": digest(native / "methods.o"),
            "executable_sha256": digest(native / "observed"),
            "native": {"exit_code": actual[0], "stdout_hex": actual[1].hex(), "stderr_hex": actual[2].hex()},
            "reference": {"exit_code": reference[0], "stdout_hex": reference[1].hex(), "stderr_hex": reference[2].hex()},
        }, indent=2) + "\n")
        assert actual == reference == (0, expected_bytes, b"")
