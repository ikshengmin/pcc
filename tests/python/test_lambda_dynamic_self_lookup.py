"""A layout-unknown self uses live lookup rather than a lexical field index."""
from pathlib import Path
import re
import pytest
from pcc.frontends.python.pipeline_context import build_closed_world_context
from pcc.frontends.python.type_infer import infer_module
from pcc.frontends.python.codegen.layer1 import L1CodeGen

MODULE = 'pcc.frontends.python.codegen.user_function_lowering'

@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('prefix_fields', [(), ('flag',), ('flag', 'other')])
@pytest.mark.parametrize('body', [
    'lambda: self.builder.call(target, [first, second], name="probe")',
    'lambda index, label: self.builder.call(target, [first, ir.Constant(ir.IntType(64), index)], name=label)',
])
def test_native_lambda_unknown_self_uses_runtime_layout(tmp_path, monkeypatch, prefix_fields, body, legacy):
    # This fixture deliberately uses the compiler module's established
    # provider contract, as the real adapter callback does. No provider or
    # result helper is replaced. It is an IR regression, not native execution.
    monkeypatch.setenv('PCC_PYTHON_IR_PASSES', 'off')
    monkeypatch.setenv('PCC_DIRECT_INDEXED_KERNEL_EMIT', '0')
    monkeypatch.setenv('PCC_DIRECT_INDEXED_KERNEL_CAPTURE', '0')
    assignments = ''.join('        self.' + field + ' = True\n' for field in prefix_fields)
    if legacy:
        body = body.replace('first', 'pair.first')
    pair_source = 'def valueclass(cls):\n    return cls\n@valueclass\nclass Pair:\n    first: int\n    second: int\n'
    source = ('from pcc.ir.compat import ir\n' + pair_source +
              'class Left:\n    def __init__(self):\n' + (assignments or '        pass\n') +
              'class UserFunctionLoweringMixin:\n'
              '    def __init__(self, builder):\n        self.builder = builder\n'
              '    def callback(self, target, first, second' + (', pair: Pair' if legacy else '') + '):\n        return ' + body + '\n'
              'class L1CodeGen(Left, UserFunctionLoweringMixin):\n'
              '    def __init__(self, builder):\n' + assignments + '        self.builder = builder\n')
    path = tmp_path / 'layout.py'; path.write_text(source)
    modules, exports, derived = build_closed_world_context([str(path)], [MODULE])
    typed = infer_module(modules[0], external_exports=exports, derived_class_map=derived)
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode='on')
    codegen._native_module_exports = exports
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    text = str(codegen.generate(typed)); (tmp_path / 'layout.ll').write_text(text)
    functions = re.findall(r'(?ms)^define[^\n]*__native_lambda_\d+\([^\n]*\).*?^}', text)
    assert len(functions) == 1
    callback = functions[0]
    if legacy:
        assert 'pair.cap' in callback
    actual = re.search(r'(%[^\s]+) = call ptr \(ptr, ptr\) @py_obj_getattr\(ptr [^,]+, ptr .*@\.pyattr\.builder', callback)
    assert actual is not None
    # Lookup must consult the actual object, not either static layout.
    assert '@py_instance_get_field(' not in callback
    assert re.search(r'@user_pcc_ir_ir_IRBuilder_call2\(ptr ' + re.escape(actual[1]) + r',', callback)
    assert '@py_cpy_' not in callback

@pytest.mark.parametrize('form,expected', [
    ('return lambda: self', 'DynType'),
    ('return lambda self: self', 'DynType'),
    ('return lambda self=self: self', 'DynType'),
    ('return lambda: (lambda: self)', 'DynType'),
    ('callback = lambda: self\n        self = replacement\n        return callback', None),
    ('callback = lambda: self\n        del self\n        return callback', None),
])
def test_capture_type_does_not_freeze_parameters_or_mutable_cells(tmp_path, monkeypatch, form, expected):
    monkeypatch.setenv('PCC_PYTHON_IR_PASSES', 'off')
    monkeypatch.setenv('PCC_DIRECT_INDEXED_KERNEL_EMIT', '0')
    monkeypatch.setenv('PCC_DIRECT_INDEXED_KERNEL_CAPTURE', '0')
    source = 'class Owner:\n    def callback(self, replacement):\n        ' + form + '\n'
    path = tmp_path / 'cells.py'; path.write_text(source)
    modules, exports, derived = build_closed_world_context([str(path)], ['cells'])
    typed = infer_module(modules[0], external_exports=exports, derived_class_map=derived)
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode='on')
    codegen._native_module_exports = exports; codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    original = codegen._emit_slot_call_operand; observations = []
    def observe(expr, label, *args, **kwargs):
        if label == 'lambda.body.result':
            slot = codegen.env.get('self')
            observations.append(type(slot[2]).__name__ if slot else None)
        return original(expr, label, *args, **kwargs)
    codegen._emit_slot_call_operand = observe
    text = str(codegen.generate(typed)); (tmp_path / 'cells.ll').write_text(text)
    if expected is None:
        # Rebinding/deletion selects the existing hoisted cell path, not
        # this adapter. Verify live lookup and its unbound-cell error edge.
        assert not observations
        body = re.search(r'(?ms)^define[^\n]*@user_cells___nested_callback\([^\n]*\).*?^}', text).group()
        assert '@py_list_getitem(' in body
        assert 'cell.bound.error' in body and '@py_exc_new(' in body
        assert '@py_instance_get_field(' not in body
    else:
        assert observations == [expected] * len(observations) and observations


def test_captured_self_does_not_hard_bind_subclass_override(tmp_path, monkeypatch):
    source = '''class Base:
    def method(self):
        return 10
    def callback(self):
        return lambda: self.method()
class Sub(Base):
    def method(self):
        return 20
base = Base()
sub = Sub()
base_callback = base.callback()
sub_callback = sub.callback()
first = (base_callback(), sub_callback())
def replacement(self):
    return 30
Base.method = replacement
second = (base_callback(), sub_callback())
'''
    oracle = {}; exec(source, oracle)
    assert oracle['first'] == (10, 20) and oracle['second'] == (30, 20)
    monkeypatch.setenv('PCC_PYTHON_IR_PASSES', 'off')
    monkeypatch.setenv('PCC_DIRECT_INDEXED_KERNEL_EMIT', '0')
    monkeypatch.setenv('PCC_DIRECT_INDEXED_KERNEL_CAPTURE', '0')
    path = tmp_path / 'dispatch.py'; path.write_text(source)
    modules, exports, derived = build_closed_world_context([str(path)], ['dispatch'])
    typed = infer_module(modules[0], external_exports=exports, derived_class_map=derived)
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode='on')
    codegen._native_module_exports = exports; codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    text = str(codegen.generate(typed)); (tmp_path / 'dispatch.ll').write_text(text)
    body = re.search(r'(?ms)^define[^\n]*__native_lambda_0\([^\n]*\).*?^}', text).group()
    assert '@user_dispatch_Base_method(' not in body
    assert '@user_dispatch_Sub_method(' not in body
    assert '@py_obj_load_method(' in body
