"""Computed attribute receivers and RHS values release on every exit."""

import os
import subprocess
import sys

import pytest

from tests.python.test_explicit_machine_arithmetic_boundaries import _pipeline_ir


PROGRAM = '''import gc
events = []
class Record:
    def __init__(self, value):
        self.value = value
    def __del__(self):
        events.append(self.value)
def direct():
    owner = Record(1)
    holders = [owner]
    holders[0].value = 11
    assert owner.value == 11
    owner = None
    holders = None
    gc.collect()
    assert events == [11]
def unpack():
    holders = [Record(2)]
    holders[0].value, other = (22, 7)
    assert holders[0].value == 22 and other == 7
    holders = None
    gc.collect()
    assert events == [11, 22]
class Value:
    def __del__(self):
        events.append("value-dead")
class Target:
    def __del__(self):
        events.append("target-dead")
    @property
    def field(self):
        return None
    @field.setter
    def field(self, value):
        events.append("setter")
        gc.collect()
        raise ValueError("original")
def make_value():
    events.append("rhs")
    return Value()
def receiver():
    events.append("receiver")
    gc.collect()
    return Target()
def bad_receiver():
    events.append("bad-receiver")
    raise KeyError("receiver-error")
def failures():
    events.clear()
    try:
        receiver().field = make_value()
    except ValueError as error:
        assert str(error) == "original"
    else:
        raise AssertionError("lost setter failure")
    gc.collect()
    assert events[:3] == ["rhs", "receiver", "setter"]
    assert events.count("value-dead") == 1
    assert events.count("target-dead") == 1
    events.clear()
    try:
        bad_receiver().field = make_value()
    except KeyError:
        pass
    else:
        raise AssertionError("lost receiver failure")
    gc.collect()
    assert events[:2] == ["rhs", "bad-receiver"]
    assert events.count("value-dead") == 1
class Secret:
    def write(self, values):
        values[0].__secret = 41
    def read(self):
        return self.__secret
def main():
    direct()
    unpack()
    failures()
    secret = Secret()
    secret.write([secret])
    assert secret.read() == 41
    print("TEMPORARY_ATTRIBUTE_OWNERS_OK")
main()
'''


def test_temporary_attribute_reference(tmp_path):
    source = tmp_path / 'attribute_reference.py'
    source.write_text(PROGRAM)
    ran = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=20)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == 'TEMPORARY_ATTRIBUTE_OWNERS_OK\n'


def test_temporary_attribute_ir_uses_owned_receiver_and_rhs(tmp_path, monkeypatch):
    text = _pipeline_ir(tmp_path, monkeypatch, PROGRAM)
    assert 'attribute.store.receiver.operand' in text
    assert 'attribute.store.value.operand' in text
    assert '@pcc_gc_frame_leave_lifo' in text


@pytest.mark.integration
def test_temporary_attribute_native_all_gc(tmp_path, monkeypatch, threaded_pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    monkeypatch.setenv('PCC_WITH_THREADS', '1')
    monkeypatch.delenv('PCC_PYTHON_IR_PASSES', raising=False)
    source = tmp_path / 'attribute_owned.py'
    source.write_text(PROGRAM)
    binary = source.with_suffix('')
    compile_python(str(source), str(binary), backend='self', libpython_mode='off', ir_scaffold_mode='on',
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                             env=dict(os.environ, PATH='/nonexistent', PCC_GC_BACKEND=str(backend),
                                      PCC_GC_REFCOUNT_PROVENANCE_PROBE='2'))
        (tmp_path / ('gc' + str(backend) + '.stdout')).write_text(ran.stdout)
        (tmp_path / ('gc' + str(backend) + '.stderr')).write_text(ran.stderr)
        assert ran.returncode == 0, (backend, ran.stdout, ran.stderr)
        assert ran.stdout == 'TEMPORARY_ATTRIBUTE_OWNERS_OK\n' and ran.stderr == ''
