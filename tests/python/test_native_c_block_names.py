"""Repeated C control-flow constructs need distinct IR block labels."""

import os
import re
import subprocess
import sys

import pytest

from tests.python.process_timeout import run_process_group_timeout


PROGRAM = """
static int classify(int x) {
    if (x < 0) return 11;
    if (x == 0) return 22;
    return 33;
}
static int loops(void) {
    int sum = 0;
    for (int i = 0; i < 3; i++) sum += i;
    for (int j = 0; j < 4; j++) sum += j;
    int n = 2;
    while (n > 0) { sum += n; n--; }
    n = 2;
    while (n > 0) { sum += n; n--; }
    return sum;
}
int main(void) {
    if (classify(-3) != 11) return 1;
    if (classify(0) != 22) return 2;
    if (classify(5) != 33) return 3;
    if (loops() != 15) return 4;
    return 0;
}
"""


@pytest.mark.parametrize("compiler_kind", [
    "pcc0", pytest.param("pcc1", marks=pytest.mark.integration),
])
def test_c_control_flow_has_unique_labels_and_executes(tmp_path, request, compiler_kind):
    command = [sys.executable, "-m", "pcc"]
    env = dict(os.environ, PCC_NO_AUTO_PCC1="1")
    env.pop("LC_ALL", None)
    if compiler_kind == "pcc1":
        command = [str(request.getfixturevalue("native_pcc1_compiler"))]
        env.update(PCC_HOST_PCC="/usr/bin/false", PCC_HOST_PYTHON="/usr/bin/false",
                   CC="/usr/bin/false")
    source = tmp_path / "branches.c"
    source.write_text(PROGRAM, encoding="utf-8")
    llvm_ir = tmp_path / "branches.ll"
    args = ["--backend", "self", "--no-cache", "-O0", str(source)]
    emitted = run_process_group_timeout(
        command + args + ["--emit-llvm=" + str(llvm_ir)], env=env, timeout=60,
    )
    assert emitted.returncode == 0, emitted.stdout + emitted.stderr
    in_function = False
    labels = set()
    functions = 0
    for line in llvm_ir.read_text().splitlines():
        if line.startswith("define "):
            in_function = True
            labels = set()
            functions += 1
        elif line == "}":
            in_function = False
        elif in_function:
            match = re.fullmatch(r"([A-Za-z_$][\w.$-]*):", line)
            if match:
                label = match.group(1)
                assert label not in labels, f"duplicate block {label!r}:\n{llvm_ir.read_text()}"
                labels.add(label)
    assert functions == 3
    output = tmp_path / "branches"
    built = run_process_group_timeout(command + args + ["-o", str(output)],
                                      env=env, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=5)
    assert ran.returncode == 0, f"rc={ran.returncode}: {ran.stdout}{ran.stderr}"
