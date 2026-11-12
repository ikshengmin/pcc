"""Closure-family syntax parses; its semantics remain explicit gaps.

Rust Reference:
https://doc.rust-lang.org/reference/trait-bounds.html (parenthesized Fn sugar)
https://doc.rust-lang.org/reference/expressions/closure-expr.html
https://doc.rust-lang.org/reference/patterns.html#tuple-struct-patterns
https://doc.rust-lang.org/error_codes/E0658.html, E0531.html
`Fn(A) -> R` sugar names a standard closure trait; a closure expression has a
unique anonymous type implementing those traits from its captures; and an
unqualified `Some(e)` pattern resolves through the value namespace to a prelude
variant. No closure type, capture, Fn-family trait or core Option/Result model
exists, so each is parsed with its source structure and then reported as an
explicit semantic gap. The unchanged fallible-streaming-iterator crate uses all
three before its next parser boundary.
"""
from pathlib import Path
import re

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import CallableSignature, Parser, Pattern
from pcc.frontends.rust.types import SharedReference, TupleType
from tests.rust.certification_support import actual_check_prerequisite, certify_source

BOUND_GAP = 'standard Fn-family closure-trait obligations require closure traits, which are not implemented'
CLOSURE_GAP = 'closure expressions require closure types, captures and Fn-family closure traits, which are not implemented'
PRELUDE_GAP = 'prelude {} variant patterns require the core Option/Result model, which is not implemented'
UNQUALIFIED_GAP = 'unqualified tuple-struct/variant patterns require value-namespace pattern resolution'
SUGAR_ONLY = 'parenthesized arguments are only Fn, FnMut and FnOnce trait sugar'

BOUNDS = (
    ('fn f<F>(f:F)where F:Fn(){}', 'Fn', CallableSignature((), '()')),
    ('fn f<F:FnMut(u64)->bool>(f:F){}', 'FnMut', CallableSignature(('u64',), 'bool')),
    ('fn f<F>(f:F)where F:FnOnce(u64,(u8,u16),)->u32{}', 'FnOnce',
     CallableSignature(('u64', TupleType(('u8', 'u16'))), 'u32')),
    ('fn f<F:core::ops::Fn(&u64)->&u64>(f:F){}', 'core::ops::Fn',
     CallableSignature((SharedReference('u64', False, None),), SharedReference('u64', False, None))),
)
CLOSURES = (
    # source, body shape, parameter names, move, explicit result
    ('fn f(){g(|e| !h(e));}', 'block(discard(call(name,closure(unary(call(name,name))))))', ['e'], False, None),
    ('fn f(){g(||1);}', 'block(discard(call(name,closure(integer))))', [], False, None),
    ('fn f(){g(move |x:u64,y,|x+y);}', 'block(discard(call(name,closure(binary(name,name)))))', ['x', 'y'], True, None),
    ('fn f(){g(|x|->u64{x});}', 'block(discard(call(name,closure(block(name)))))', ['x'], False, 'u64'),
    ('fn f(){g(|(a,b)|a).h(|r|r);}',
     'block(discard(call(field(call(name,closure(name))),closure(name))))', [None], False, None),
)
PATTERNS = (
    ('fn f(){while let Some(e)=g(){}}', 'Some', 1),
    ('fn f(){match g(){Ok(x)=>x,Err(_)=>0};}', 'Ok', 1),
    ('fn f(){if let Local(a,b,)=g(){}}', 'Local', 2),
)
# Valid Rust: rustc accepts each unchanged original; PCC keeps an explicit gap.
VALID = (
    ('where-fn-mut', 'pub fn f<F>(f:F)->u64 where F:FnMut(u64)->bool{0}', BOUND_GAP),
    ('inline-ops-path', 'pub fn f<F:core::ops::Fn(u64,u8,)->(u64,u8)>(f:F)->u64{0}', BOUND_GAP),
    ('fn-once-unit', 'pub fn f<F:FnOnce()>(f:F){}', BOUND_GAP),
    ('closure-argument', 'fn twice(x:u64)->u64{x*2}pub fn f()->u64{let g=|x:u64|twice(x);0}', CLOSURE_GAP),
    ('some-pattern', 'pub fn f(x:Option<u64>)->u64{match x{Some(v)=>v,None=>0}}',
     "unsupported Rust type 'Option'|" + re.escape(PRELUDE_GAP.format('Option'))),
    ('project-default-method-shape',
     'pub trait It{type Item;type Error;fn next(&mut self)->Result<Option<&Self::Item>,Self::Error>;'
     'fn all<F>(&mut self,mut f:F)->Result<bool,Self::Error> where Self:Sized,F:FnMut(&Self::Item)->bool'
     '{while let Some(e)=self.next()?{if !f(e){return Ok(false);}}Ok(true)}'
     'fn any<F>(&mut self,mut f:F)->Result<bool,Self::Error> where Self:Sized,F:FnMut(&Self::Item)->bool'
     '{self.all(|e|!f(e)).map(|r|!r)}}', re.escape(BOUND_GAP) + "|unsupported Rust type"),
)
# Invalid Rust. rustc 1.91.1 rejects each with the given code (parenthesized
# arguments on a non-Fn-family trait are E0658 with E0107/E0220); so does PCC.
INVALID = (
    ('sugar-on-marker', 'pub fn f<T:Copy(u64)>(x:T){}', SUGAR_ONLY, 'E0658'),
    ('sugar-on-local-trait', 'pub trait L{} pub fn f<T:L(u64)>(x:T){}', SUGAR_ONLY, 'E0658'),
    ('local-trait-named-fn', 'pub trait Fn{} pub fn f<T:Fn(u64)>(x:T){}', 'local trait', 'E0658'),
    ('unknown-unqualified-variant', 'pub enum E{A(u64)} pub fn f(e:E)->u64{match e{A(x)=>x}}',
     UNQUALIFIED_GAP, 'E0531'),
)


def shape(expr):
    children = [child for child in (expr.children or []) if hasattr(child, 'kind')]
    return expr.kind + ('(' + ','.join(shape(child) for child in children) + ')' if children else '')


def closures(expr):
    found = [expr] if expr.kind == 'closure' else []
    for child in expr.children or []:
        if hasattr(child, 'kind'):
            found.extend(closures(child))
    return found


@pytest.mark.parametrize('source,path,signature', BOUNDS, ids=[row[1] + str(i) for i, row in enumerate(BOUNDS)])
def test_fn_family_sugar_retains_its_signature(source, path, signature):
    function, = Parser(source).parse()
    (_, (bound,)), = function.effective_parameter_bounds
    assert (bound.path, bound.relaxed, bound.callable) == (path, False, signature)


@pytest.mark.parametrize('source,message', [
    ('fn f<T:Copy(u64)>(x:T){}', SUGAR_ONLY),
    ('fn f<T>(x:T)where T:Iterator(u64){}', SUGAR_ONLY),
    ('fn f<T:?Fn()>(x:T){}', 'only Sized bounds can be relaxed'),
    ('fn f<T:Fn(u64>(x:T){}', "expected"),
])
def test_parenthesized_arguments_on_other_bounds_are_rejected(source, message):
    with pytest.raises(RustError, match=re.escape(message)):
        Parser(source).parse()


def test_ordinary_bounds_have_no_callable_signature():
    function, = Parser('fn f<T:Copy+?Sized>(x:&T){}').parse()
    (_, bounds), = function.effective_parameter_bounds
    assert [(bound.path, bound.relaxed, bound.callable) for bound in bounds] == [
        ('Copy', False, None), ('Sized', True, None)]


@pytest.mark.parametrize('source,expected,names,moves,result', CLOSURES, ids=[row[1] for row in CLOSURES])
def test_closure_expressions_keep_parameters_and_body(source, expected, names, moves, result):
    function, = Parser(source).parse()
    assert shape(function.body) == expected
    closure = closures(function.body)[0]
    signature = closure.value
    assert [pattern.token.text if pattern.kind == 'binding' else None
            for pattern, _ in signature.parameters] == names
    assert (signature.moves, signature.result) == (moves, result)


def test_closure_parameter_annotations_are_retained():
    function, = Parser('fn f(){g(move |x:u64,y,|x+y);}').parse()
    signature = closures(function.body)[0].value
    assert [annotation for _, annotation in signature.parameters] == ['u64', None]


@pytest.mark.parametrize('source', ['fn f(){g(|x|->u64 x);}', 'fn f(){g(|x:u64|);}', 'fn f(){g(|x x);}'])
def test_malformed_closures_are_parse_errors(source):
    with pytest.raises(RustError):
        Parser(source).parse()


@pytest.mark.parametrize('source,name,arity', PATTERNS, ids=[row[1] for row in PATTERNS])
def test_unqualified_tuple_patterns_keep_their_segment_and_fields(source, name, arity):
    function, = Parser(source).parse()
    pending = [function.body]
    patterns = []
    while pending:
        node = pending.pop()
        value = node.value
        # while/if let keep one pattern; a match keeps one pattern per arm.
        patterns.extend(item for item in (value if isinstance(value, list) else [value])
                        if isinstance(item, Pattern))
        pending.extend(child for child in node.children or [] if hasattr(child, 'kind'))
    pattern = next(p for p in patterns if p.kind == 'payload')
    assert (pattern.value, len(pattern.children)) == ((None, name), arity)


def test_unit_like_names_remain_binding_patterns():
    # Only the parenthesized form is a tuple pattern; `None` keeps its former
    # identifier-pattern parse and is not reinterpreted by this slice.
    function, = Parser('fn f(x:u64){match x{None=>0};}').parse()
    (pattern,) = function.body.children[0].children[0].value
    assert (pattern.kind, pattern.token.text) == ('binding', 'None')


@pytest.mark.parametrize('source,message', [
    ('pub fn f<F>(f:F)->u64 where F:FnMut(u64)->bool{0}', BOUND_GAP),
    ('pub fn f<F:core::ops::FnOnce()>(f:F){}', BOUND_GAP),
    ('pub fn f<F:std::ops::Fn()>(f:F){}', BOUND_GAP),
    ('pub fn f<F:core::marker::Fn()>(f:F){}', 'standard marker path is unavailable'),
    ('pub fn f()->u64{let g=||1;0}', CLOSURE_GAP),
    ('pub enum E{A(u64)} pub fn f(e:E)->u64{match e{A(x)=>x}}', UNQUALIFIED_GAP),
    ('pub enum E{A(u64)} pub fn f(e:E)->u64{match e{Some(x)=>x,_=>0}}', PRELUDE_GAP.format('Option')),
    ('pub enum E{A(u64)} pub fn f(e:E)->u64{match e{Err(x)=>x,_=>0}}', PRELUDE_GAP.format('Result')),
], ids=['where', 'core-ops', 'std-ops', 'not-marker-module', 'closure', 'unqualified', 'some', 'err'])
def test_closure_family_semantics_stay_explicit_gaps(source, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('closure-family source reached IR'))
    with pytest.raises(RustError, match=re.escape(message)):
        compile_rust(source, experimental_rustc_checked=True)


def test_local_trait_named_fn_rejects_sugar_in_semantic_analysis():
    # rustc E0658 (+E0107/E0220). The experimental public route stops earlier at the existing
    # local-trait certification gate; analysis reaches the bound itself.
    from tests.rust.analysis_only import analyze_source
    with pytest.raises(RustError, match=re.escape('a local trait does not accept them')):
        analyze_source('pub trait Fn{} pub fn f<T:Fn(u64)>(x:T){}', moves=False)


def test_original_project_lines_are_unchanged():
    root = Path(__file__).resolve().parents[2]
    lines = (root / 'projects/fallible-streaming-iterator-0.1.9/src/lib.rs').read_text().splitlines()
    assert lines[66] == '        F: FnMut(&Self::Item) -> bool,'
    assert lines[68] == '        while let Some(e) = self.next()? {'
    assert lines[83] == '        self.all(|e| !f(e)).map(|r| !r)'
    assert lines[207] == '        for _ in 0..n {'
    assert lines[305] == "impl<'a, I: ?Sized> FallibleStreamingIterator for &'a mut I"


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_accepts_closure_family_originals_with_explicit_owned_gap(tmp_path):
    for name, text, message in VALID:
        checked = certify_source(text, tmp_path / name)
        with pytest.raises(RustError, match=message if '|' in message else re.escape(message)):
            checked.compile_owned()


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_invalid_closure_family_originals(tmp_path, monkeypatch):
    for name, text, message, code in INVALID:
        with pytest.raises(RustError, match=re.escape(message)):
            compile_rust(text, experimental_rustc_checked=True)
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
