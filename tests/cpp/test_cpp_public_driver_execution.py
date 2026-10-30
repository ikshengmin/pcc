"""Actual public CLI object emission and owned ELF execution (host compiler).

No pcc1/bootstrap or full-runtime final-link qualification is implied. A tiny
owned startup adapter calls the public main symbol and returns its exit status.
"""
import subprocess

import pytest

from tests.cpp.owned_execution import Execution, assert_exact, run_executable

pytestmark = pytest.mark.integration

CASES = [
    ("if-character", "#if U'a' < -1\nint main(){return 42;}\n#else\nint main(){return 9;}\n#endif", 42),
    ("if-unsigned", "#if -1 < 1U\nint main(){return 9;}\n#else\nint main(){return 42;}\n#endif", 42),
    ("if-ternary-type", "#if (true ? -1 : 1U) > 0\nint main(){return 42;}\n#else\nint main(){return 9;}\n#endif", 42),
    ("if-integer-division", "#if (-9223372036854775807 / 3) == -3074457345618258602 and (-9223372036854775807 % 3) == -1\nint main(){return 42;}\n#else\nint main(){return 9;}\n#endif", 42),
    ("if-wrap", "#if (18446744073709551615U + 1) == 0\nint main(){return 42;}\n#else\nint main(){return 9;}\n#endif", 42),
    ("if-true", "#if true\nint main(){return 42;}\n#else\nint main(){return 9;}\n#endif", 42),
    ("if-false", "#if false\nint main(){return 9;}\n#else\nint main(){return 42;}\n#endif", 42),
    ("if-alternative", "#define YES true\n#if YES and not false and ((3 bitand 1) not_eq 0)\nint main(){return 42;}\n#else\nint main(){return 9;}\n#endif", 42),
    ("overload", "int pick(int x){return x+1;} int pick(double x){return 9;} int main(){return pick(41);}", 42),
    ("template-loop", "template<typename T> T count(T x){T n=0;while(x>0){x=x-1;if(x==2)continue;n=n+1;}return n;} int main(){return count(8);}", 7),
    ("bool", "bool truth(double x){return bool(x);} int main(){return int(truth(0.5))+int(truth(-0.0))*2;}", 1),
    ("fallthrough", "int main(){}", 0),
]


def link_object(data):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file
    startup = assemble_file(
        ".intel_syntax noprefix\n.text\n.globl _start\n_start:\n"
        "  call main\n  mov edi,eax\n  mov rax,60\n  syscall\n"
    )
    return link_static_executable([startup, parse_relocatable(data)], entry="_start")


@pytest.mark.parametrize("name,source,expected", CASES)
def test_public_cli_emits_stable_owned_cpp_object_and_executes(tmp_path, monkeypatch, name, source, expected):
    from pcc.driver.cli_core import cli_main
    unit = tmp_path / (name + ".cpp")
    unit.write_text(source)
    objects = []
    for index in range(2):
        output = tmp_path / (str(index) + ".o")
        with monkeypatch.context() as patch:
            def forbidden(*args, **kwargs):
                pytest.fail("public object emission spawned an external tool")
            patch.setattr(subprocess, "Popen", forbidden)
            assert cli_main([str(unit), "--emit-obj", str(output),
                             "--no-cache", "-O0", "--target=" + "x86_64-unknown-linux-gnu"]) == 0
        objects.append(output.read_bytes())
    assert objects[0] == objects[1]
    executable = tmp_path / "owned"
    executable.write_bytes(link_object(objects[0]))
    executable.chmod(0o700)
    actual = run_executable(executable)
    assert_exact(actual, Execution(expected, b"", b""))
    # Explicit external oracle; it is never an owned-build fallback.
    oracle = tmp_path / "oracle"
    result = subprocess.run(["g++", "-std=c++17", "-O0", str(unit), "-o", str(oracle)],
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert_exact(actual, run_executable(oracle))


def test_public_cli_diagnostic_keeps_unsupported_cpp_unpublished(tmp_path, capsys):
    from pcc.driver.cli_core import cli_main
    unit = tmp_path / "unsupported.cpp"
    unit.write_text("namespace detail { int f(){return 1;} } int main(){return detail::f();}")
    output = tmp_path / "unsupported.o"
    assert cli_main([str(unit), "--emit-obj", str(output), "--no-cache", "-O0"]) == 1
    assert not output.exists()
    assert "cpp." in capsys.readouterr().err


def test_cpp_dependency_public_route_never_uses_system_cpp(tmp_path, monkeypatch):
    from pcc.driver.cli_core import cli_main
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    main = tmp_path / 'main.cpp'
    helper = tmp_path / 'helper.cpp'
    main.write_text('int helper(int x); int main(){return helper(41);}')
    helper.write_text('int helper(int x){return x+1;}')
    output = tmp_path / 'combined.o'
    with monkeypatch.context() as patch:
        def forbidden(*args, **kwargs):
            pytest.fail('C++ dependency path invoked an external tool')
        patch.setattr(CEvaluator, '_system_cpp', forbidden)
        patch.setattr(subprocess, 'Popen', forbidden)
        assert cli_main([str(main), '--depends-on', str(helper), '--jobs', '1',
                         '--emit-obj', str(output), '--no-cache', '-O0']) == 0
    executable = tmp_path / 'owned-dependency'
    executable.write_bytes(link_object(output.read_bytes()))
    executable.chmod(0o700)
    actual = run_executable(executable)
    assert_exact(actual, Execution(42, b'', b''))
    oracle = tmp_path / 'oracle-dependency'
    result = subprocess.run(['g++', '-std=c++17', '-O0', str(main), str(helper), '-o', str(oracle)],
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert_exact(actual, run_executable(oracle))
