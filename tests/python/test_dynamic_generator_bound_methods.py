"""Dynamic/stored generator methods retain the receiver and real protocol."""
import os
import subprocess
import sys

from pcc.frontends.python.pipeline import compile_python


PROVIDER = '''def make():
    try:
        value = yield 10
        try:
            yield value
        except ValueError as error:
            yield error
    finally:
        print("finalized")
'''

PROGRAM = '''import generator_provider

def main():
    generator = generator_provider.make()
    send = getattr(generator, "send")
    throw = getattr(generator, "throw")
    close = getattr(generator, "close")
    print(send.__self__ is generator)
    del generator
    try:
        send()
    except TypeError:
        print("send arity")
    try:
        close(1)
    except TypeError:
        print("close arity")
    print(send(None))
    try:
        throw(123)
    except TypeError:
        print("throw type")
    value = ["payload"]
    print(send(value) is value)
    error = ValueError("token")
    print(throw(error) is error)
    print(close(), close())
    try:
        send(None)
    except StopIteration:
        print("exhausted")
main()
'''

EXPECTED = "True\nsend arity\nclose arity\n10\nthrow type\nTrue\nTrue\nfinalized\nNone None\nexhausted\n"


def test_dynamic_generator_bound_methods_native_five_gc(tmp_path, pcc_runtime_archive):
    (tmp_path / "generator_provider.py").write_text(PROVIDER)
    source = tmp_path / "generator_consumer.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True,
                               text=True, timeout=15)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, EXPECTED, "")
    output = tmp_path / "generator_consumer"
    compile_python(str(source), str(output), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for gc in range(5):
        result = subprocess.run([str(output)], capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc)))
        assert (result.returncode, result.stdout, result.stderr) == (0, EXPECTED, ""), gc
