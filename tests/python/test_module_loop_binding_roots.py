"""Module loop names use persistent, initialized namespace owners."""
import re

import pytest

from tests.python.test_shared_call_binding import _emit
from tests.python.owned_regression_support import explicit_owned_runtime


@pytest.mark.parametrize('loop', (
    "for item in [{'name': 'first'}, {'name': 'last'}]:\n    result = item.get('name')\n",
    "if True:\n    for item in [{'name': 'last'}]:\n        result = item.get('name')\n",
    "try:\n    for item in [{'name': 'last'}]:\n        result = item.get('name')\nexcept ValueError:\n    pass\n",
    "for item in ({'name': 'last'},):\n    result = item.get('name')\n",
))
def test_module_loop_object_reads_have_a_real_global_root(loop,tmp_path):
    text=_emit(loop)
    (tmp_path/'program.ll').write_text(text)
    assert bool(re.search(r'^@\.modvar\.binding\.item = global ptr null$', text, re.M)), 'missing module target object slot'
    assert '@.modvar.binding.item_initialized' in text
    assert re.search(r'\bcall [^\n]*@pcc_gc_root_copy_lease\([^\n]*',text)
    assert 'item.for.obj.addr' not in text
    assert '.pcc.for.binding.module' in text


@pytest.mark.parametrize('body', (
    "for item in []:\n    pass\nprint(item)\n",
    "item = 9\nfor item in []:\n    pass\nprint(item)\n",
    "for item in range(3):\n    pass\nprint(item)\n",
    "for left, item in [(1, {'name': 'last'})]:\n    result = item.get('name')\n",
))
def test_loop_binding_storage_and_bound_flag_cover_join_edges(body,tmp_path):
    text=_emit(body);(tmp_path/'program.ll').write_text(text)
    assert bool(re.search(r'^@\.modvar\.binding\.item = global ptr null$', text, re.M)), 'missing module target object slot'
    assert '@.modvar.binding.item_initialized' in text
    assert 'item.for.obj.addr' not in text


def test_function_defined_before_loop_reads_the_same_module_binding(tmp_path):
    text=_emit("def read():\n    return item.get('name')\nfor item in [{'name': 'last'}]:\n    result = read()\n")
    (tmp_path/'program.ll').write_text(text)
    body=re.search(r'^define [^\n]*@user_binding_read\([^\n]*\).*?^}',text,re.M|re.S).group(0)
    assert '@.modvar.binding.item' in body
    flag = re.search(r'(%[\w.$]+) = load i1, ptr @\.modvar\.binding\.item_initialized', body)
    assert flag is not None, 'missing authoritative module binding flag'
    assert re.search(r'br i1 ' + re.escape(flag[1]) + r', label %[^,]+, label %', body), 'binding flag does not guard the read'


PROGRAM = "import gc\n\nevents = []\n\nclass Token:\n    def __init__(self, name):\n        self.name = name\n    def __del__(self):\n        events.append(self.name)\n        gc.collect()\n\ndef read_item():\n    gc.collect()\n    return item\n\nfor item in [Token('first'), Token('last')]:\n    assert read_item() is item\n    gc.collect()\nassert read_item().name == 'last'\nassert events == ['first']\ndel item\ngc.collect()\nassert events == ['first', 'last']\n\nprior = 19\nfor prior in []:\n    raise AssertionError('empty loop ran')\nassert prior == 19\nfor missing in []:\n    pass\ntry:\n    missing\nexcept NameError:\n    pass\nelse:\n    raise AssertionError('empty loop created a binding')\n\nfor number, item in [(7, Token('unpacked'))]:\n    assert number == 7 and read_item() is item\nassert read_item().name == 'unpacked'\ndel item\ngc.collect()\nassert events[-1] == 'unpacked'\n\nfor number in range(3):\n    pass\nassert number == 2\nif True:\n    for item in [{'name': 'nested'}]:\n        assert item.get('name') == 'nested'\nassert read_item().get('name') == 'nested'\ndel item\ntry:\n    read_item()\nexcept NameError:\n    pass\nelse:\n    raise AssertionError('deleted module loop binding remained visible')\nprint('MODULE_LOOP_BINDING_OK')\n"


def test_module_loop_binding_reference_program(capsys):
    exec(compile(PROGRAM, '<module-loop-reference>', 'exec'), {})
    assert capsys.readouterr().out == 'MODULE_LOOP_BINDING_OK\n'


@pytest.mark.integration
@pytest.mark.parametrize('python_program_compiler', ('pcc0', 'pcc1'), indirect=True)
def test_module_loop_binding_native_five_gc(python_program_compiler, request,
                                            explicit_owned_runtime, tmp_path, capfd):
    from tests.python.owned_regression_support import assert_owned_program
    mode = request.node.callspec.params['python_program_compiler']
    assert_owned_program(PROGRAM, 'MODULE_LOOP_BINDING_OK\n', tmp_path,
                         python_program_compiler, mode, explicit_owned_runtime,
                         capfd, provenance_probe='2')
    assert (tmp_path / 'compiler-wrapper.stderr').read_text() == ''
