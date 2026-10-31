"""Byte-string constants, private descriptors and owned comparison loops."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from pcc.frontends.go import GoDiagnostic, compile_go, function_symbol
from pcc.frontends.go.strings import literal_bytes
from pcc.frontends.go.syntax import scan
from tests.go.owned_execution import Execution, compile_owned, compile_reference, run, write_artifact
from tests.go.test_execution import forbid_external_compiler


def source(body, declarations=""):
    return "package main;"+(declarations+";" if declarations else "")+"func main(){"+body+"}"


def original_units():
    return (("strings.go", (Path(__file__).parent/"fixtures/strings.go").read_bytes().decode("utf-8")),)


@pytest.mark.parametrize("spelling,expected", [
    ('""',b""), ('"é🙂"',"é🙂".encode()), (r'"\000\377\x00\xff"',b"\x00\xff\x00\xff"),
    (r'"\a\b\f\n\r\t\v\\\""',b'\a\b\f\n\r\t\v\\"'),
    (r'"\u00e9\U0001f642"',"é🙂".encode()), ('`a\r\nb`',b'a\nb'),
    ('"a\rb"',b'a\rb'), ('`a\\nb`',b'a\\nb'), ('`\"`',b'"'),
])
def test_literal_decoder_distinguishes_bytes_unicode_and_raw_carriage_returns(spelling,expected):
    assert literal_bytes(scan(spelling)[0])==expected


@pytest.mark.parametrize("spelling,message", [
    (r'"\x0"',"escape digits"), (r'"\xgg"',"escape digits"), (r'"\12"',"escape digits"),
    (r'"\400"',"overflows byte"), (r'"\777"',"overflows byte"), (r'"\8"',"invalid string escape"),
    (r'"\uD800"',"Unicode scalar"), (r'"\U00110000"',"Unicode scalar"),
    (r'"\u000"',"escape digits"), (r'"\z"',"invalid string escape"),
    ('"a\nb"',"newline"), ('"\x00"',"NUL character"), ('"\ud800"',"Unicode scalar"),
    ('"\\\'"',"invalid string escape"),
])
def test_invalid_original_string_spelling_fails_located(spelling,message):
    with pytest.raises(GoDiagnostic,match=message):
        compile_go(source("_="+spelling))


@pytest.mark.parametrize("body,declarations,message", [
    ('const x="a"[0];_=x',"","not a constant"),
    ('var x int="a"[0];_=x',"","cannot use uint8 as int"),
    ('_="a"[-1]',"","nonnegative"), ('_="a"[1]',"","out of bounds"),
    ('_=""[0]',"","out of bounds"), ('_="a"[true]',"","must be an integer"),
    ('i:=0;_="a"[i]',"","bounds-panic"), ('s:="a";_=s[0]',"","bounds-panic"),
    ('var x uint64;_="a"[x]',"","bounds-panic"), ('"a"[0]=1',"","non-addressable"),
    ('len("a")',"","only function calls"),
    ('var x uint8=len("abc");_=x',"","cannot use int as uint8"),
    ('const N=len("abc");var x int64=N;_=x',"","cannot use int as int64"),
    ('s:="a";_=s+"b"',"","allocation"), ('x:=65;_=string(x)',"","allocation"),
    ('_=string(true)',"","invalid conversion"), ('_=uint64("a")',"","invalid conversion"),
    ('_="a"-"b"',"","unsupported string operator"), ('_="a"+1',"","mismatched string"),
    ('_=len(1)',"","len currently supports strings"), ('_=len()',"","requires one argument"),
    ('_=[1]string{"a"}',"","string-element arrays"), ('_=[...]string{"a"}',"","non-scalar array elements"),
    ('', 'var S=Identity("a");func Identity(s string)string{return s}', "shared-runtime initialization"),
])
def test_string_unsupported_operations_never_reach_ir(body,declarations,message,monkeypatch):
    from pcc.frontends.go.lowering import Lowerer
    monkeypatch.setattr(Lowerer,"lower",lambda *args:pytest.fail("invalid string reached lowering"))
    with pytest.raises(GoDiagnostic,match=message):
        compile_go(source(body,declarations))


def test_string_precision_limit_is_a_diagnostic_not_unbounded_constant_concatenation():
    from pcc.frontends.go.strings import MAX_STRING_BYTES
    with pytest.raises(GoDiagnostic,match="1 MiB"):
        compile_go(source('_="'+('x'*(MAX_STRING_BYTES+1))+'"'))


def test_string_descriptor_has_target_pointer_and_length_and_immutable_backing():
    module=compile_go(original_units()[0][1])
    descriptor=module.globals[function_symbol("Identity")].function_type.return_type
    assert len(descriptor.elements)==2
    assert hasattr(descriptor.elements[0],"pointee") and descriptor.elements[1].width==64
    storage=[item for name,item in module.globals.items() if name.startswith("__pcc_go_string_")]
    assert storage and all(item.global_constant and item.linkage=="private" for item in storage)
    text=str(module)
    assert "string.check" in text and "icmp ult" in text
    assert "declare " not in text


RAW_STRINGS = [("Byte",(index,),value,1) for index,value in enumerate(bytes.fromhex("00ff41c3a9f09f9982090a5c2278797a"))] + [
    ("LiteralLength",(),16,8), ("RawLength",(),7,8), ("RawByte",(),206,1),
    ("ValueLength",(),16,8), ("ZeroLength",(),0,8), ("MultipleLength",(),23,8),
    ("Snapshot",(),4,8), ("GlobalLength",(),5,8), ("LenOnce",(),116,8),
    ("CompareOrder",(),12,8), ("Prefix",(),-1,8), ("Unsigned",(),1,8),
    ("Nul",(),-1,8), ("Unicode",(),1,8), ("EmptyFirst",(),-1,8), ("Equal",(),0,8),
    ("Relations",(),11,8), ("Replacement",(),0xbdbfef,8), ("Codepoint",(),0x82999ff0,8), ("Folded",(),4,8),
]


@pytest.mark.integration
@pytest.mark.parametrize("name,args,expected,width",RAW_STRINGS)
def test_original_string_helpers_have_independent_exact_native_bytes(name,args,expected,width,tmp_path,monkeypatch):
    original=original_units()
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        artifact=compile_owned(original,observe_entry=(name,args))
    binary=tmp_path/"strings-native";write_artifact(artifact,binary)
    actual=run(binary);raw=(expected&((1<<(width*8))-1)).to_bytes(width,"little")
    (tmp_path/"raw-strings-observation.json").write_text(json.dumps({
        "owner":artifact.owner,"function":name,"arguments":args,"expected_hex":raw.hex(),
        "stdout_hex":actual.stdout.hex(),"returncode":actual.returncode,"stderr_hex":actual.stderr.hex(),
        "source_sha256":hashlib.sha256(original[0][1].encode()).hexdigest(),
        "executable_sha256":hashlib.sha256(artifact.executable).hexdigest(),
    },indent=2)+"\n")
    assert actual==Execution(0,raw,b"")


@pytest.mark.integration
def test_original_string_main_executes_owned_native(tmp_path,monkeypatch):
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied);artifact=compile_owned(original_units())
    binary=tmp_path/"strings-main";write_artifact(artifact,binary)
    assert run(binary)==Execution(0,b"",b"")


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
def test_official_go_checks_the_identical_complete_string_program(tmp_path):
    original=original_units()
    binary=compile_reference(original,tmp_path/"reference",compiler=os.environ["PCC_GO_REFERENCE"],cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert run(binary)==Execution(0,b"",b"")
    assert (binary.parent/"strings.go").read_bytes()==original[0][1].encode()


def test_original_string_program_has_real_darwin_cross_object(monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    forbid_external_compiler(monkeypatch)
    module=compile_go(original_units()[0][1],target_triple="arm64-apple-darwin")
    assert emit_owned_object(str(module),"arm64-apple-darwin")[:4]==b"\xcf\xfa\xed\xfe"


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
def test_raw_and_interpreted_source_carriage_returns_keep_original_bytes(tmp_path,monkeypatch):
    original=source('if len(`a\r\nb`)!=3 || "a\rb"[1]!=13 {for{}}')
    units=(("carriage.go",original),)
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied);artifact=compile_owned(units)
    binary=tmp_path/"carriage-native";write_artifact(artifact,binary)
    reference=compile_reference(units,tmp_path/"reference",compiler=os.environ["PCC_GO_REFERENCE"],cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert run(binary)==run(reference)==Execution(0,b"",b"")
    assert (reference.parent/"carriage.go").read_bytes()==original.encode()


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
@pytest.mark.parametrize("body,pcc_message,reference_message", [
    ('const x="a"[0];_=x', "not a constant", "not constant"),
    ('var x int="a"[0];_=x', "cannot use uint8 as int", "cannot use.*as int"),
    ('_="a"[1]', "out of bounds", "out of bounds"),
    ('_="a"[-1]', "nonnegative", "must not be negative"),
    ('len("a")', "only function calls", "not used"),
    ('var x uint8=len("abc");_=x', "cannot use int as uint8", "cannot use.*as uint8"),
    ('const N=len("abc");var x int64=N;_=x', "cannot use int as int64", "cannot use.*as int64"),
    ('_=string(true)', "invalid conversion", "cannot convert"),
])
def test_official_go_rejects_identical_invalid_string_semantics(body,pcc_message,reference_message,tmp_path):
    original=source(body)
    with pytest.raises(GoDiagnostic,match=pcc_message):
        compile_go(original)
    with pytest.raises(AssertionError,match=reference_message):
        compile_reference((("invalid.go",original),),tmp_path/"reference",
                          compiler=os.environ["PCC_GO_REFERENCE"],cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert (tmp_path/"reference/invalid.go").read_bytes()==original.encode()
    assert not (tmp_path/"reference/oracle").exists()


def test_constant_string_length_keeps_int_type_and_allows_explicit_conversion():
    module=compile_go(source('const N=len("abc");var x uint8=uint8(N);_=x'))
    assert "store i8 3" in str(module)


def test_runtime_string_descriptors_load_relocated_static_values_not_inline_pointer_aggregates():
    module=compile_go('package main;var S string="abc";func Result()string{return "abc"};func main(){_=Result()}')
    descriptors=[item for name,item in module.globals.items() if name.startswith("__pcc_go_string_value_")]
    assert len(descriptors)==1 and descriptors[0].global_constant
    assert descriptors[0].linkage=="private"
    text=str(module)
    function_text=text[text.index("define "):]
    # Global data owns relocation; executed instructions only load the value.
    assert "getelementptr inbounds (" not in function_text
    assert any("load " in line and "__pcc_go_string_value_0" in line for line in function_text.splitlines())


# Preserve the exact failed b8 reference input as invalid source. The valid
# carriage-return test above is a separate source, not a retroactive PASS.
FAILED_CARRIAGE_ORIGINAL = 'package main;;func main(){if len(`a\r\nb`)!=3 || "a\rb"[1]!=13 {for{}}}'
INVALID_SEPARATOR_ORIGINALS = [
    FAILED_CARRIAGE_ORIGINAL,
    'package main;;func main(){}',
    'package main;func main(){};;',
    'package main;const A=1;;func main(){_=A}',
    'package main;import(;);func main(){}',
    'package main; import();;func main(){}',
]


@pytest.mark.parametrize("original",INVALID_SEPARATOR_ORIGINALS)
def test_empty_package_or_import_declarations_are_rejected_not_stripped(original):
    with pytest.raises(GoDiagnostic,match="empty (package declarations|import specifications)"):
        compile_go(original)


def test_empty_block_statements_and_empty_declaration_groups_remain_valid():
    assert compile_go('package main;import();const();var();func main(){;;;const();var();;}')


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
@pytest.mark.xdist_group(name="go-reference")
@pytest.mark.parametrize("original",INVALID_SEPARATOR_ORIGINALS)
def test_official_go_rejects_the_same_retained_invalid_separator_originals(original,tmp_path):
    with pytest.raises(GoDiagnostic,match="empty (package declarations|import specifications)"):
        compile_go(original)
    reference_message = "missing import path" if original == 'package main;import(;);func main(){}' else "syntax error"
    with pytest.raises(AssertionError,match=reference_message):
        compile_reference((("invalid.go",original),),tmp_path/"reference",
                          compiler=os.environ["PCC_GO_REFERENCE"],cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert (tmp_path/"reference/invalid.go").read_bytes()==original.encode()
    assert not (tmp_path/"reference/oracle").exists()
