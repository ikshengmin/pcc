"""Decode inputs and both dynamic outputs survive fallible owner cleanup."""
import ast
from pathlib import Path
import re

import pytest

from pcc.frontends.python.codegen import string_method_lowering
from tests.python.test_lambda_adapter_scope_lifetime import check_frames
from tests.python.test_lambda_constructor_roots import emit


CASES = {
    'typed_default': ('data: bytes', 'data.decode()', False),
    'typed_ascii': ('data: bytes', 'data.decode("ascii")', False),
    'bytearray': ('data: bytearray', 'data.decode("utf-8", "ignore")', False),
    'dynamic': ('data', 'data.decode("ascii")', True),
    'encoding_factory': ('data', 'data.decode(make("utf-8"))', True),
    'errors_factory': ('data', 'data.decode(errors=make("ignore"))', True),
    'keyword_order': ('data', 'data.decode(errors=make("ignore"), encoding=make("utf-8"))', True),
    'receiver_factory': ('data', 'make(data).decode("ascii")', True),
    'late_argument_error': ('data', 'data.decode(make("ascii"), fail())', True),
    'strip_consumer': ('data: bytes', 'data.decode("ascii").strip()', False),
    'original_ar_shape': ('data: bytes', 'int(data[48:58].decode("ascii").strip())', False),
    'tuple_consumer': ('data', '(data.decode("ascii"),)', True),
    'default_consumer': ('data', 'data.decode()', True),
}


def physical_body(body):
    aliases=dict(re.findall(r'(%[-\w.]+) = bitcast ptr (%[-\w.]+) to ptr',body))
    def resolve(match):
        value=match.group(0);seen=set()
        while value in aliases:
            assert value not in seen
            seen.add(value);value=aliases[value]
        return value
    return re.sub(r'%[-\w.]+',resolve,body)


@pytest.mark.parametrize('case',CASES)
def test_bytes_decode_publishes_before_error_check_and_cleanup(case):
    parameter,expression,dynamic=CASES[case]
    tail='    return take(value='+expression+')\n'
    if case=='default_consumer':
        tail='    def kept(value='+expression+'):\n        return value\n    return kept\n'
    text=emit('def take(*, value):\n    return value\ndef make(value):\n    return value\ndef fail():\n    raise ValueError("later decoder argument")\ndef probe('+parameter+'):\n'+tail)
    body=next(body for body in re.findall(r'^define[^\n]*\{\n.*?^}',text,re.M|re.S) if '@user_lambda_owned_probe(' in body.splitlines()[0])
    body=physical_body(body)
    call=re.search(r'(%[-\w.]+) = call ptr[^\n]*@py_bytes_decode_with_encoding\([^\n]*\n([^\n]+)',body)
    assert call is not None
    publication=re.search(r'store ptr '+re.escape(call.group(1))+r', ptr (%[-\w.]+)',call.group(2))
    assert publication is not None
    output=publication.group(1)
    arguments=re.search(r'@py_bytes_decode_with_encoding\(ptr (%[-\w.]+), ptr (%[-\w.]+), ptr (%[-\w.]+)\)',call.group(0))
    assert arguments is not None
    for value in arguments.groups():
        loaded=re.search(re.escape(value)+r' = load ptr, ptr (%[-\w.]+)',body[:call.start()])
        assert loaded is not None
        slot=loaded.group(1)
        assert re.search(r'@pcc_gc_foreign_lease_acquire\(ptr '+re.escape(slot)+r'\)',body[:call.start()]) is not None
    assert 'dyn.decode.result' not in body
    assert re.search(r'@pcc_gc_store_root\(ptr %decode.receiver[^\n]*ptr null\)',body) is not None
    if dynamic:
        assert re.search(r'@py_obj_call_slots\(ptr [^,]+, ptr [^,]+, ptr [^,]+, ptr '+re.escape(output)+r'\)',body) is not None
        lookup=re.search(r'(%[-\w.]+) = call ptr[^\n]*@py_obj_getattr\([^\n]*\n([^\n]+)',body)
        assert lookup is not None and 'store ptr '+lookup.group(1) in lookup.group(2)


@pytest.mark.parametrize('expression', ['data.decode()', 'data.decode(errors="ignore", encoding="utf-8")', 'make(data).decode(make("ascii"))', 'data.decode(make("ascii"), fail())'])
def test_dynamic_decode_both_branches_have_balanced_error_and_normal_frames(expression):
    text=emit('def make(value):\n    return value\ndef fail():\n    raise ValueError("option")\ndef probe():\n    return lambda data: '+expression+'\n')
    body=next(body for body in re.findall(r'^define[^\n]*\{\n.*?^}',text,re.M|re.S) if re.search(r'@user_lambda_owned__native_lambda_\d+\(',body.splitlines()[0]))
    check_frames(body)


@pytest.mark.parametrize('tag,encoding,errors,expected', [
    (1,'ascii','strict','str'), (2,'ascii','ignore','str'),
    (1,'utf-8','strict','utf8'), (2,'utf-8','ignore','ignore'),
    (1,'utf-8','surrogateescape','surrogateescape'),
    (1,'latin-1','strict','error'), (1,'utf-8','replace','error'),
    (99,'ascii','strict','error'),
])
def test_actual_decoder_body_new_return_and_existing_capability_dispatch(tag,encoding,errors,expected):
    # Caller leases are proved in emitted IR above. This executes the actual
    # exported decoder body and checks its terminal NEW return convention and
    # current capability branches without pretending to implement a codec.
    source=Path(string_method_lowering.__file__).parents[3]/'runtime/py/py_obj_stubs.py'
    tree=ast.parse(source.read_text())
    function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='py_bytes_decode_with_encoding')
    function.decorator_list=[]
    output=object();calls=[]
    class Value:
        def __init__(self,kind,value):self.kind,self.value=kind,value
    receiver=Value(tag,b'abc');enc=Value(3,encoding);err=Value(3,errors)
    def new(kind):
        assert not calls or calls[-1]!='new'
        calls.extend([kind,'new']);return output
    namespace={
        'PY_TYPE_BYTES':1,'PY_TYPE_BYTEARRAY':2,'PY_TYPE_MEMORYVIEW':4,'PY_TYPE_STR':3,
        'ptr_is_null':lambda value:value is None,'null':lambda:None,'is_tagged_int':lambda value:False,
        '_type_of':lambda value:value.kind,'_str_is_ascii_name':lambda value:value.value=='ascii',
        '_str_is_utf8_name':lambda value:value.value=='utf-8',
        '_str_is_errors_name':lambda value,index:value.value==('strict','ignore')[index],
        '_str_is_surrogateescape':lambda value:value.value=='surrogateescape',
        '_bytes_data':lambda value:value.value,'py_bytes_len':lambda value:len(value.value),
        'load_i8':lambda data,index:data[index],'ptr_eq':lambda a,b:a is b,'global_load_ptr':lambda name:None,
        'cstr':lambda value:value,'py_exc_new':lambda kind,message:(kind,message),
        'py_raise_owned':lambda error:calls.append(('error',error)),
        'py_str_new':lambda data,length:new('str'),'py_bytes_decode':lambda value:new('utf8'),
        'py_bytes_decode_utf8_ignore':lambda value:new('ignore'),
        'py_bytes_decode_utf8_surrogateescape':lambda value:new('surrogateescape'),
    }
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),namespace)
    result=namespace['py_bytes_decode_with_encoding'](receiver,enc,err)
    if expected=='error':
        assert result is None and calls and calls[-1][0]=='error'
    else:
        assert result is output and calls==[expected,'new']


def test_bytes_decode_reference_binding_and_error_behavior():
    events=[]
    class Decoder:
        @property
        def decode(self):
            events.append('lookup')
            def invoke(*args,**kwargs):
                events.append(('invoke',args,kwargs));return 'done'
            return invoke
    def make(label,value):events.append(label);return value
    assert make('receiver',Decoder()).decode(make('encoding','ascii'),errors=make('errors','strict'))=='done'
    assert events==['receiver','lookup','encoding','errors',('invoke',('ascii',),{'errors':'strict'})]
    assert bytearray(b' 42 ').decode('ascii').strip()=='42'
    assert b' 42 '.decode(errors='ignore',encoding='utf-8').strip()=='42'
    with pytest.raises(TypeError):b'abc'.decode(None)
