"""Empty call turbofish shares omitted-argument inference, not owner inference.

Rust Reference:
https://doc.rust-lang.org/reference/paths.html#paths-in-expressions
https://doc.rust-lang.org/reference/expressions/call-expr.html
https://doc.rust-lang.org/reference/expressions/method-call-expr.html
https://doc.rust-lang.org/reference/expressions.html#evaluation-order-of-operands
Actual source/options certification remains mandatory for reference-bearing
inputs. Empty nominal owners, constructor segments and type annotations stay
outside this call-only slice; no new borrow, ABI or runtime owner is introduced.
"""
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.syntax import Parser
from pcc.support.instantiation import InstanceState
from tests.rust.certification_support import actual_check_prerequisite, certify_source

PROGRAMS=('empty_call_values','empty_call_methods','empty_call_modules','empty_call_references')
ID='fn id<T>(x:T)->T{x}'
VALID=(
    'fn value()->u64{42}fn f()->u64{value::<>()}',
    ID+'fn f()->u64{id::<>(4294967296)}',
    ID+'fn take(x:u64){}fn f(){let x=id::<>(4294967296);take(x);}',
    ID+'fn f()->i32{id::<>(1)}',
    ID+'fn f()->(u64,bool){id::<>((4294967296,true))}',
    ID+'fn f()->[u64;2]{id::<>([4294967296,1])}',
    ID+'fn wrap<U>(x:U)->U{id::<>(x)}',
    'fn empty<T>()->[T;0]{[]}fn f()->[u64;0]{empty::<>()}',
    'fn pair<A,B>(a:A,b:B)->(A,B){(a,b)}fn f()->(u64,bool){pair::<>(4294967296,true)}',
    'struct R{x:u64}impl R{fn take(self)->u64{self.x}}fn f()->u64{R{x:4294967296}.take::<>()}',
    'struct R<T>{x:T}impl<T>R<T>{fn take<U>(self,u:U)->U{u}}fn f()->bool{R::<u64>::take::<>(R{x:1u64},true)}',
    'struct R{x:u64}impl R{fn make(x:u64)->Self{Self{x:x}}}fn f()->R{R::make::<>(4294967296)}',
    'fn f()->usize{[1u64,2].len::<>()}',
    'trait Read{fn read(self)->u64;}struct R{x:u64}impl Read for R{fn read(self)->u64{self.x}}fn f()->u64{<R as Read>::read::<>(R{x:4294967296})}',
    'mod m{pub fn id<T>(x:T)->T{x}}fn f()->u64{m::id::<>(4294967296)}',
    'unsafe fn raw()->u64{42}fn f()->u64{unsafe{raw::<>()}}',
)
CERTIFIED_VALID=(
    'trait Get{fn get(self)->u64;}struct R{x:u64}'
    'impl Get for R{fn get(self)->u64{self.x}}'
    'fn read<T:Get>(x:T)->u64{x.get::<>()}'
    'fn f()->u64{read::<>(R{x:4294967296})}',
)
INVALID=(
    ('ambiguous','fn unused<T>(){}fn f(){unused::<>();}','cannot infer','E0282'),
    ('operand-arity',ID+'fn f(){id::<>();}','expects 1 arguments','E0061'),
    ('result-conflict',ID+'fn f()->bool{id::<>(1u64)}','type mismatch','E0308'),
    ('inconsistent','fn same<T>(a:T,b:T){}fn f(){same::<>(1u64,2u32);}','type mismatch','E0308'),
    ('overflow',ID+'fn f()->i32{id::<>(4294967296)}','out of range','literal out of range'),
    ('noncopy-move',ID+'struct R{x:u64}fn f(){let r=R{x:1u64};id::<>(r);r;}','moved','E0382'),
    ('abstract-move',ID+'fn bad<U>(x:U)->U{let y=id::<>(x);x}','moved','E0382'),
    ('nonempty-nongeneric','fn value()->u64{42}fn f()->u64{value::<u64>()}','nongeneric function','E0107'),
    ('nonempty-arity','fn pair<A,B>(a:A,b:B){}fn f(){pair::<u64>(1u64,true);}','argument count','E0107'),
    ('array-len-arguments','fn f()->usize{[1u64].len::<u64>()}','not a generic method','E0107'),
    ('missing-function','fn f(){missing::<>();}','unknown function','E0425'),
    ('unsafe-context','unsafe fn raw()->u64{42}fn f()->u64{raw::<>()}','requires an unsafe context','E0133'),
    ('local-shadow',ID+'fn f(){let id=1u64;id::<>(1);}','only unshadowed direct function calls','E0618'),
)
# These unchanged Rust-valid originals retain narrower owned capability gates.
CAPABILITIES=(
    ('struct S<T>{x:T} fn f(){S::<>{x:1};}','empty/inferred'),
    ('struct R<T>{x:T}impl<T>R<T>{fn new(x:T)->Self{Self{x:x}}}fn f()->R<u64>{R::<>::new(1)}','empty/inferred'),
    ('enum E<T>{Some(T)}fn f()->E<u64>{E::Some::<>(1)}','constructor segments'),
    (ID+'fn f(x:&u64){id::<>(x);}','generic function reference arguments'),
)


def source(name):
    return Path(__file__).with_name('programs').joinpath(name+'.rs').read_text()


@pytest.mark.parametrize('text',VALID)
def test_empty_call_arguments_share_existing_constraints(text):
    compile_rust(text,experimental_rustc_checked=True)


def test_empty_explicit_and_omitted_calls_share_one_instance_registry():
    text=ID+'fn f(){let a=id::<>(1u64);let b=id::<u64>(a);let c=id(b);}'
    parser=Parser(text)
    checker=Checker(parser.parse(),parser.records,parser.enums,parser.implementations)
    checker.check()
    entries=tuple(checker.function_generics.instances.entries.values())
    assert len(entries)==1
    assert entries[0].value.instance_arguments==('u64',)
    assert entries[0].state is InstanceState.COMPLETE


def test_empty_lists_restore_parser_depth_and_preserve_suffix_presence():
    parser=Parser('fn value(){}fn f(){'+'value::<>();'*40+'}')
    functions=parser.parse()
    assert parser.type_argument_depth==0
    statements=functions[-1].body.children
    assert len(statements)==40 and all(statement.kind=='discard' for statement in statements)
    assert all(statement.children[0].kind=='call' and
               statement.children[0].children[0].value.arguments==() for statement in statements)
    compile_rust(parser.source,experimental_rustc_checked=True)
    suffix=Parser('struct R{x:u64}impl R{fn take(self)->u64{self.x}}fn f()->u64{R{x:1}.take::<>()}')
    function=next(function for function in suffix.parse() if function.token.text=='f')
    assert function.body.children[-1].children[0].type_arguments==()
    assert suffix.type_argument_depth==0


@pytest.mark.parametrize('text,message',(
    ('struct R<T>{x:T}fn f(x:R<>){}','empty/inferred'),
    (ID+'fn f(){let saved=id::<>;}','empty/inferred'),
    ('struct R{x:u64}impl R{fn take(self)->u64{self.x}}fn f(){let saved=R{x:1}.take::<>;}','require a direct call'),
))
def test_empty_type_annotations_and_stored_function_values_remain_closed(text,message,monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('non-call empty arguments reached lowering'))
    with pytest.raises(RustError,match=message):
        compile_rust(text,experimental_rustc_checked=True)


@pytest.mark.parametrize('name,text,message,code',INVALID)
def test_invalid_empty_calls_fail_before_lowering(name,text,message,code,monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('invalid empty call reached lowering'))
    with pytest.raises(RustError,match=message):
        compile_rust(text,experimental_rustc_checked=True)


@pytest.mark.parametrize('text,message',CAPABILITIES)
def test_empty_lists_do_not_expand_owner_constructor_or_reference_substitution(text,message):
    from tests.rust.analysis_only import analyze_source
    with pytest.raises(RustError,match=message):
        analyze_source(text)


def test_empty_turbofish_does_not_admit_reference_source_with_a_boolean_flag():
    with pytest.raises(RustError,match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(PROGRAMS[-1]),experimental_rustc_checked=True)


@pytest.mark.parametrize('text',CERTIFIED_VALID)
def test_empty_calls_retain_actual_certification_for_local_bound_replay(text):
    with pytest.raises(RustError,match='actual source/options-bound CheckedRustSource'):
        compile_rust(text,experimental_rustc_checked=True)


@pytest.mark.parametrize('name',PROGRAMS[:-1])
@pytest.mark.parametrize('target,magic',[
    ('x86_64-unknown-linux-gnu',b'\x7fELF'),('arm64-apple-darwin',b'\xcf\xfa\xed\xfe'),
])
def test_empty_call_originals_emit_owned_objects(name,target,magic):
    assert compile_rust(source(name),experimental_rustc_checked=True,target_triple=target).emit_object().startswith(magic)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine() in {'x86_64','AMD64'} else 'requires owned Linux x86-64 execution')
@pytest.mark.parametrize('name',PROGRAMS[:-1])
def test_empty_call_originals_execute_owned_full_width(name,tmp_path,monkeypatch):
    from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution
    test_original_match_fixture_full_width_owned_execution(name,tmp_path,monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_accepts_exact_empty_call_originals(tmp_path):
    for index,text in enumerate(VALID):
        certify_source(text,tmp_path/str(index))
    for index,text in enumerate(CERTIFIED_VALID):
        checked=certify_source(text,tmp_path/('bound-'+str(index)))
        compiled=checked.compile_owned()
        assert compiled.externally_checked and not compiled.borrow_checked_by_pcc
    checked=certify_source(source(PROGRAMS[-1]),tmp_path/'reference')
    compiled=checked.compile_owned()
    assert compiled.externally_checked and not compiled.borrow_checked_by_pcc
    for index,(text,message) in enumerate(CAPABILITIES):
        checked=certify_source(text,tmp_path/('capability-'+str(index)))
        with pytest.raises(RustError,match=message):
            checked.compile_owned()


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_invalid_empty_calls(tmp_path,monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle,'INVALID',INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path,monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (None if sys.platform.startswith('linux') and platform.machine() in {'x86_64','AMD64'} and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc and system linker'))
def test_empty_call_originals_match_actual_rustc(tmp_path,monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference,'PROGRAMS',PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path,monkeypatch)
