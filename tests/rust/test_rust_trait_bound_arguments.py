"""Generic arguments and associated item constraints in trait bounds.

https://doc.rust-lang.org/reference/trait-bounds.html
https://doc.rust-lang.org/reference/paths.html#paths-in-expressions (GenericArgs)
`I: Iterator<Item = Result<&'a T, E>>` (fallible-streaming-iterator's
`convert`) parses: lifetimes, type arguments and `Name = Type` constraints
are retained in source order on the bound. No trait or projection solving
consumes them, so every semantic use is an explicit boundary. Each verdict
below was compared with local rustc 1.92.0.
"""
import shutil
import subprocess

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from pcc.frontends.rust.types import TypeApplication

GAP = 'generic arguments and associated item constraints on trait bounds require trait/projection solving'


def bounds(source):
    function, = Parser(source).parse()
    return {name.text: items for name, items in function.effective_parameter_bounds}


def test_arguments_and_constraints_are_retained_in_source_order():
    found = bounds("fn f<'a, I, T, E>(it: I) where I: Iterator<Item = Result<&'a T, E>>,"
                   " T: Tr<'a, u8, Item = u64, Error = E> + Copy {}")
    iterator, = found['I']
    assert (iterator.path, iterator.lifetimes, iterator.arguments) == ('Iterator', (), ())
    constraint, = iterator.constraints
    assert constraint.name.text == 'Item' and isinstance(constraint.type, TypeApplication)
    assert constraint.type.name == 'Result' and str(constraint.type) == "Result<&'a T, E>"
    local, copy = found['T']
    assert (local.lifetimes, local.arguments) == (("'a",), ('u8',))
    assert [(item.name.text, item.type) for item in local.constraints] == [('Item', 'u64'), ('Error', 'E')]
    assert local.generic and not copy.generic


def test_nested_closing_angles_are_split_in_bound_arguments():
    found = bounds('fn f<I>(i: I) where I: Iterator<Item = Option<Option<u8>>> {}')
    constraint, = found['I'][0].constraints
    assert str(constraint.type) == 'Option<Option<u8>>'


@pytest.mark.parametrize('source,message', [
    # rustc: generic arguments must come before the first constraint
    ('fn f<T: Tr<Item = u64, u8>>(t: T) {}', 'generic arguments must come before associated item constraints'),
    # rustc E0747: type provided when a lifetime was expected
    ("fn f<'a, T: Tr<u8, 'a, Item = u64>>(t: T) {}", r'lifetime arguments must be provided before type arguments \(rustc E0747\)'),
])
def test_misordered_bound_arguments_are_parse_errors(source, message):
    with pytest.raises(RustError, match=message):
        Parser(source).parse()


@pytest.mark.parametrize('source,message', [
    ('pub fn f<I: Iterator<Item = u64>>(i: I) {}', GAP),
    ('pub fn f<T>(t: T) where T: Iterator<Item = u64> {}', GAP),
    ('pub fn f<T: Copy<u8>>(t: T) {}', GAP),
    # Valid since Rust 1.79 (associated type bounds), but not modeled here.
    ('pub fn f<T: Iterator<Item: Copy>>(t: T) {}', 'associated type bounds are not implemented'),
    ('pub fn f<T: Fn(u8)<u8>>(t: T) {}', 'Fn-family sugar takes no further generic arguments'),
])
def test_semantic_uses_stay_explicit(source, message):
    with pytest.raises(RustError, match=message):
        compile_rust(source, experimental_rustc_checked=True)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_gives_the_same_verdicts(tmp_path):
    cases = (
        ("pub trait Tr<T> { type Item; } pub fn f<T: Tr<u8, Item = u64>>(_t: T) {}", None),
        ("pub fn f<'a, I, T: 'a, E>(_i: I) where I: Iterator<Item = Result<&'a T, E>> {}", None),
        ("pub fn f<T: Iterator<Item: Copy>>(_t: T) {}", None),
        ("pub trait Tr<T> { type Item; } pub fn f<T: Tr<Item = u64, u8>>(_t: T) {}",
         'generic arguments must come before the first constraint'),
        ("pub trait Tr<'a, T> { type Item; } pub fn f<'a, T: Tr<u8, 'a, Item = u64>>(_t: T) {}", 'error[E0747]'),
    )
    for index, (source, expected) in enumerate(cases):
        path = tmp_path / f'case{index}.rs'
        path.write_text(source + '\n')
        run = subprocess.run([shutil.which('rustc'), '--crate-type=lib', '--edition=2021', '--emit=metadata',
                              str(path), '-o', str(tmp_path / f'case{index}.rmeta')],
                             capture_output=True, text=True, timeout=60)
        if expected is None:
            assert run.returncode == 0, run.stderr
        else:
            assert run.returncode != 0 and expected in run.stderr, run.stderr
