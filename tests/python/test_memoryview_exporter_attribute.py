"""memoryview.obj returns a NEW owner of the true exporter (Python built-in types)."""
import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.owned_regression_support import (
    assert_owned_program, assert_reference_program, explicit_owned_runtime,
)
from tests.python.runtime_body_model import load_functions
from tests.python.test_buffer_factory_ownership import BufferModel, RUNTIME


def _views(kind, depth, relocate):
    m = BufferModel(kind, relocate=relocate)
    m.frames[m.external] = depth + 2
    for n in range(1, depth + 2):
        m.store(m.external, n * 8, None)
    for n in range(depth):
        # Model a caller immediately publishing each NEW into its own root.
        view = m.ns['py_memoryview_new'](m.load(m.external, n * 8))
        m.store(m.external, (n + 1) * 8, view)
        m.boundary()
    return m


def _get(m, depth):
    def owned_exporter(o):
        load_functions(RUNTIME, {'py_memoryview_exporter'}, m.ns)
        return m.ns['py_memoryview_exporter'](o)
    def missing(o, name):
        raise AttributeError(name)
    # Execute the real attribute dispatch. The receiver is an exact builtin;
    # foreign-type hooks and unrelated name checks cannot supply its attribute.
    ns = {key: val for key, val in vars(abi).items() if key.startswith('PY_')}
    ns.update(i64=int, null=lambda: None, ptr_is_null=lambda v: v is None,
              load_i32=lambda o, offset: abi.PY_TYPE_MEMORYVIEW,
              is_tagged_int=lambda o: False, cstr=str, strcmp=lambda a,b: (a>b)-(a<b),
              pcc_diagnostics_runtime_log_event_code=lambda *args: None,
              _cstr_is_dunder_class=lambda n: False, _cstr_is_pop=lambda n: False,
              pcc_capi_type_object_getattr=lambda *args: None,
              pcc_capi_builtin_object_getattr=lambda *args: None,
              pcc_capi_is_cext_type_tag=lambda tag: False, _is_instance_tag=lambda tag: False,
              py_err_occurred=lambda: m.pending is not None,
              _raise_attribute_error=missing, py_memoryview_exporter=owned_exporter)
    load_functions(RUNTIME.with_name('py_obj_ops_dispatch.py'), {'py_obj_getattr'}, ns,
                   local_helpers=False)
    return ns['py_obj_getattr'](m.load(m.external, depth * 8), 'obj')


@pytest.mark.parametrize('kind', ('bytes','bytearray'))
@pytest.mark.parametrize('depth', (1,2,5))
@pytest.mark.parametrize('relocate', (False,True))
def test_exporter_is_owned_after_all_view_roots_leave(kind, depth, relocate):
    m = _views(kind, depth, relocate)
    original = m.load(m.external, 0)[0]
    got = _get(m, depth)
    out = (depth + 1) * 8
    m.store(m.external, out, got)
    assert got[0] is original
    for n in range(depth + 1):
        m.clear_root(m.external + n * 8, None)
    m.boundary()
    result = m.load(m.external, out)
    assert result[0].refs == 1 and m.content(result) == b'ABC'
    m.clear_root(m.external + out, None)
    assert not m.leases and not m.pins and len(m.frames) == 1
    assert all(o.refs == 0 for o in m.objects)


@pytest.mark.parametrize('fail_at', (1,2,3,4,5))
def test_exporter_lookup_failure_leaves_view_and_exporter_owned(fail_at):
    m = _views('bytearray', 2, True)
    before = [v.refs for v in m.objects]
    calls = []
    def copy(dst, src):
        calls.append(None)
        return -1 if len(calls) == fail_at else m.copy(dst, src)
    m.ns.update(pcc_gc_root_copy_borrowed_lease=copy, pcc_gc_root_copy_lease=copy)
    assert _get(m, 2) is None and m.pending is not None
    assert len(calls) == fail_at and not m.leases and not m.pins
    assert [v.refs for v in m.objects[:len(before)]] == before
    assert m.content(m.load(m.external, 16)) == b'ABC'


PROGRAM = """import gc

def owner(view):
    return view.obj

def just_owner():
    return memoryview(memoryview(bytearray(b'246'))).obj

def main():
    b = b'123'
    vb = memoryview(b)
    nb = memoryview(vb)
    print(vb.obj is b, nb.obj is b, owner(nb) is b)
    data = bytearray(b'123')
    direct = memoryview(data)
    nested = memoryview(direct)
    print(direct.obj is data, nested.obj is data, getattr(nested, 'obj') is data)
    alias = owner(nested)
    alias[0] = 57
    print(bytes(data), int(nested))
    keep = just_owner()
    gc.collect()
    print(isinstance(keep, bytearray), bytes(keep))
    print(getattr(data, 'obj', None) is None)

main()
"""
EXPECTED = "True True True\nTrue True True\nb'923' 923\nTrue b'246'\nTrue\n"


def test_exporter_attribute_public_reference(tmp_path):
    assert_reference_program(PROGRAM, EXPECTED, tmp_path)


@pytest.mark.integration
def test_exporter_attribute_public_native(python_program_compiler, request,
                                          explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params['python_program_compiler']
    assert_owned_program(PROGRAM, EXPECTED, tmp_path, python_program_compiler, mode,
                         explicit_owned_runtime, capfd)
