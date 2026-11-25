"""Qualify one explicit C++17 reference compiler before its full platform gate.

The normative compiler must implement the accepted CWG2394 correction. An
independent compiler observation records its own outcomes, never replaces a
failed selected compiler or qualifies PCC. No source-test filtering occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


OPTIONS = ("-std=c++17", "-pedantic-errors", "-O0")
IDENTITY_FIELDS = ("compiler_invocation", "compiler_resolved", "compiler_sha256",
                   "version", "target")
# CWG2394 (accepted DR, February 2019): a const member that is itself
# const-default-constructible does not delete the enclosing default C1.
# The converse exercises real rejection, not a permissive compiler mode.
PROBES = (
    ("const-member",
     "struct Empty{};struct Outer{const Empty empty;};"
     "int main(){const Outer o;return 42;}\n", True),
    ("uninitialized-scalar",
     "struct Nonempty{int value;};struct Outer{const Nonempty member;};"
     "int main(){const Outer o;return 0;}\n", False),
)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def observe_process(command, directory, name):
    try:
        result = subprocess.run(command, capture_output=True, timeout=15, check=False)
        stdout, stderr = result.stdout, result.stderr
        row = {"command": command, "status": "COMPLETE", "returncode": result.returncode}
    except (OSError, subprocess.TimeoutExpired) as error:
        stdout = getattr(error, "stdout", None) or b""
        stderr = getattr(error, "stderr", None) or b""
        row = {"command": command, "status": type(error).__name__,
               "returncode": None, "error": str(error)}
    (directory / (name + ".stdout.bin")).write_bytes(stdout)
    (directory / (name + ".stderr.bin")).write_bytes(stderr)
    row.update(stdout_sha256=hashlib.sha256(stdout).hexdigest(),
               stderr_sha256=hashlib.sha256(stderr).hexdigest())
    return row


def observe_compiler(invocation, output, *, role):
    """Keep both positive and negative observations and exact tool identity."""
    output.mkdir(parents=True, exist_ok=False)
    row = {"role": role, "compiler_invocation": invocation}
    path = Path(invocation)
    if not path.is_absolute() or not path.is_file():
        row.update(status="UNRUN_PREREQUISITE", reason="explicit compiler executable unavailable")
    else:
        # Hash the executable's bytes but do not invoke the resolved clang
        # path in place of clang++; driver/link semantics depend on spelling.
        row.update(compiler_resolved=str(path.resolve()), compiler_sha256=digest(path))
        identity_ok = True
        for option, name in (("--version", "version"), ("-dumpmachine", "target")):
            observation = observe_process([invocation, option], output, name)
            row[name] = (output / (name + ".stdout.bin")).read_text(errors="replace")
            row[name + "_observation"] = observation
            identity_ok &= observation["returncode"] == 0
        row["probes"] = []
        for name, source, accepts in PROBES:
            directory = output / name
            directory.mkdir()
            unit = directory / "source.cpp"
            unit.write_text(source)
            binary = directory / "program"
            command = [invocation, *OPTIONS]
            command.extend([str(unit), "-o", str(binary)] if accepts
                           else ["-fsyntax-only", str(unit)])
            compile_result = observe_process(command, directory, "compile")
            probe = {"name": name, "source_sha256": digest(unit),
                     "expected": "accept-and-run" if accepts else "reject",
                     "compile": compile_result}
            if accepts:
                ok = compile_result["returncode"] == 0
                if ok:
                    execution = observe_process([str(binary)], directory, "execution")
                    probe["execution"] = execution
                    ok = (execution["returncode"] == 42
                          and (directory / "execution.stdout.bin").read_bytes() == b""
                          and (directory / "execution.stderr.bin").read_bytes() == b"")
            else:
                # A signal, timeout, unavailable compiler or missing diagnostic
                # is not an observation of the required semantic rejection.
                code = compile_result["returncode"]
                ok = (code is not None and code > 0
                      and bool((directory / "compile.stderr.bin").read_bytes()))
            probe["matches"] = ok
            row["probes"].append(probe)
        qualified = identity_ok and all(probe["matches"] for probe in row["probes"])
        row["status"] = "CAPABILITY_CONFIRMED" if qualified else "CAPABILITY_MISMATCH"
    (output / "receipt.json").write_text(json.dumps(row, indent=2) + "\n")
    return row


def require_normative(row, identity):
    if row["role"] != "normative" or row["status"] != "CAPABILITY_CONFIRMED":
        raise ValueError("selected C++17 normative reference did not satisfy the accepted DR")
    if any(row.get(key) != identity.get(key) or key not in identity for key in IDENTITY_FIELDS):
        raise ValueError("reference compiler identity differs from the original bound tool")
    version = re.search(r"\bclang version (\d+)\.", row["version"])
    if version is None or int(version.group(1)) < 15:
        raise ValueError("C++17 normative platform reference requires Clang 15 or later")


def qualify(compiler, diagnostic_driver, output, identity):
    output.mkdir(parents=True, exist_ok=False)
    normative = observe_compiler(compiler, output / "normative", role="normative")
    receipt = {"normative": normative}
    if diagnostic_driver:
        diagnostic = shutil.which(diagnostic_driver)
        if diagnostic is None:
            receipt["diagnostic"] = {"role": "diagnostic", "status": "UNRUN_PREREQUISITE",
                                     "driver": diagnostic_driver}
        else:
            receipt["diagnostic"] = observe_compiler(
                diagnostic, output / "diagnostic", role="diagnostic")
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("C++17 normative reference capability:", normative["status"], flush=True)
    if diagnostic_driver:
        print("Independent reference diagnostic (not a normative verdict):",
              receipt["diagnostic"]["status"], flush=True)
        if receipt["diagnostic"]["status"] == "UNRUN_PREREQUISITE":
            raise ValueError("configured independent reference observation unavailable")
    require_normative(normative, identity)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--identity", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--diagnostic-driver", default="")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("the unchanged reference suite command is required")
    qualify(args.compiler, args.diagnostic_driver, args.out,
            json.loads(args.identity.read_text()))
    # Replace this process only after qualification: the original process-tree
    # guard still owns all subsequent reference executions and their failures.
    environment = dict(os.environ, PCC_CPP_ORACLE=args.compiler)
    os.execvpe(command[0], command, environment)


if __name__ == "__main__":
    main()
