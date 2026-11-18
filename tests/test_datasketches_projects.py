"""Apache DataSketches in C++, Rust, Go and Java plus its Python package.

projects/datasketches-* pin the unchanged releases (scripts/datasketches_sources.py,
reproduced by scripts/pin_datasketches.py): one sketch library in the four PCC
source languages and its nanobind Python package, the shared acceptance target
for compiling the same algorithms with every frontend and comparing their
performance. These nodes verify every pinned byte and record each project's
first observed blocker under the owned frontends; a frontier node failing
because the frontier moved is progress to record, never a regression to mask.
"""
import shutil

import pytest

from scripts.datasketches_sources import LOCK_SHA256, PROJECTS, RELEASES, ROOT, load_project, text_units

RUST_FEATURES = ('bloom', 'countmin', 'cpc', 'frequencies', 'hll', 'kll', 'req', 'tdigest', 'theta', 'tuple')
GO_MODULE = 'github.com/apache/datasketches-go'


@pytest.mark.parametrize('key', sorted(PROJECTS))
def test_every_pinned_byte_and_the_exact_file_set_verify(key):
    lock, files = load_project(key)
    assert lock['revision'] == RELEASES[key]['revision'] and lock['license'] == 'Apache-2.0'
    assert len(files) == len(lock['files']) and sum(map(len, files.values())) == lock['total_bytes']
    assert {'LICENSE', 'NOTICE'} <= set(files)
    assert b'Licensed to the Apache Software Foundation' in files['NOTICE'] or b'Apache' in files['NOTICE']
    assert len(set(LOCK_SHA256.values())) == len(PROJECTS)


def test_dependency_closures_are_recorded_as_absent():
    for key in PROJECTS:
        lock, _ = load_project(key)
        for group in ('runtime', 'test', 'build'):
            for dependency in lock['dependencies'].get(group, ()):
                assert dependency.get('source_closure_present', False) is False or 'pinned_as' in dependency


def copy_project(key, tmp_path):
    copy = tmp_path / PROJECTS[key]
    shutil.copytree(ROOT / 'projects' / PROJECTS[key], copy, symlinks=True)
    return copy


def test_a_changed_byte_is_rejected(tmp_path):
    copy = copy_project('go', tmp_path)
    target = copy / 'hll/hll_sketch.go'
    target.write_bytes(target.read_bytes().replace(b'package hll', b'package hlm', 1))
    with pytest.raises(ValueError, match='checksum/size/bound mismatch: hll/hll_sketch.go'):
        load_project('go', copy)


def test_an_unpinned_file_is_rejected(tmp_path):
    copy = copy_project('cpp', tmp_path)
    (copy / 'hll/include/extra.hpp').write_text('#pragma once\n')
    with pytest.raises(ValueError, match='missing or unpinned files: hll/include/extra.hpp'):
        load_project('cpp', copy)


def test_upstream_symbolic_links_stay_links_inside_the_pin(tmp_path):
    lock, files = load_project('rust')
    links = [entry['path'] for entry in lock['files'] if entry.get('symlink')]
    assert links == ['datasketches/LICENSE', 'datasketches/NOTICE']
    assert files['datasketches/LICENSE'] == b'../LICENSE'
    copy = copy_project('rust', tmp_path)
    link = copy / 'datasketches/LICENSE'
    link.unlink()
    link.write_bytes((copy / 'LICENSE').read_bytes())
    with pytest.raises(ValueError, match='pinned symbolic link was replaced'):
        load_project('rust', copy)


def test_rust_crate_stops_at_its_first_shift_operator():
    from pcc.frontends.rust import RustError
    from pcc.frontends.rust.configuration import CfgOptions, OwnedRustConfiguration
    from pcc.frontends.rust.crate_context import parse_crate_sources
    _, files = load_project('rust')
    units = {path.removeprefix('datasketches/'): text
             for path, text in text_units(files, ('.rs',), under='datasketches/src/').items()}
    options = CfgOptions(cfg=tuple(f'feature="{name}"' for name in RUST_FEATURES),
                         debug_assertions=False, panic='unwind')
    configuration = OwnedRustConfiguration('aarch64-apple-darwin', 2024, options)
    # Past the owned `#![deny(missing_docs)]` lint (stage 8): shift operators
    # are not parsed yet.
    with pytest.raises(RustError, match=r"^src/lib\.rs: src/bloom/mod\.rs: src/bloom/sketch\.rs: 33:31: expected ';', found '<<'"):
        parse_crate_sources('src/lib.rs', units, crate_name='datasketches', edition=2024,
                            configuration=configuration)


def go_packages(files):
    from collections import defaultdict
    packages = defaultdict(list)
    for path, text in text_units(files, ('.go',)).items():
        if path.endswith('_test.go') or path.startswith('examples/'):
            continue
        directory, _, name = path.rpartition('/')
        packages[GO_MODULE + ('/' + directory if directory else '')].append((name, text))
    return dict(packages)


def test_go_module_stops_at_an_instantiated_generic_type():
    from pcc.frontends.go import UnsupportedGo, compile_go_packages
    from scripts.go_std_closure import load_std_closure
    _, files = load_project('go')
    packages = go_packages(files)
    assert len(packages) == 19
    _, std = load_std_closure()
    graph = {**std, **packages}
    imports = ''.join(f'import _ "{path}"\n' for path in sorted(packages) if path != GO_MODULE)
    graph['main'] = [('m.go', 'package main\n' + imports + 'func main() {}\n')]
    # common/item_sketch_double.go: `func(reverseOrder bool) CompareFn[float64]`.
    with pytest.raises(UnsupportedGo, match=r'^item_sketch_double\.go:33:58: Go: instantiated types require') as caught:
        compile_go_packages(graph, target_triple='x86_64-unknown-linux-gnu')
    assert caught.value.code == 'go.unsupported.generic-boundaries'


def test_java_production_units_exceed_the_source_unit_limit():
    from pcc.frontends.java import JavaError, analyze_class_sources, compile_sources
    _, files = load_project('java')
    units = text_units(files, ('.java',), under='src/main/java/')
    assert len(units) == 374
    # Both owned paths stop before parsing: 374 units exceed the 256-unit
    # input limit, reported as invalid input rather than a capability code.
    with pytest.raises(JavaError, match='class source model requires between 1 and 256 supplied units'):
        analyze_class_sources(units)
    with pytest.raises(JavaError, match='source project requires between 1 and 256 supplied units'):
        compile_sources(units, entry_class='org.apache.datasketches.hll.HllSketch')


def preprocess(key, source, base, extra_includes=()):
    from pcc.frontends.cpp.driver import preprocess_cpp
    cpp = ROOT / 'projects' / PROJECTS['cpp']
    includes = [str(path) for path in sorted(cpp.glob('*/include'))] + list(extra_includes)
    return preprocess_cpp(source, base_dir=str(base), cpp_args=['-std=c++17'] + [f'-I{path}' for path in includes],
                          target_triple='x86_64-unknown-linux-gnu', source_name=str(base / 'unit.cpp'))


@pytest.mark.parametrize('header,missing', [
    ('hll.hpp', 'cstdint'), ('theta_sketch.hpp', 'vector'), ('kll_sketch.hpp', 'memory'), ('cpc_sketch.hpp', 'iostream'),
])
def test_cpp_headers_stop_at_the_missing_standard_library(header, missing):
    load_project('cpp')
    # common_defs.hpp:23 includes <cstdint>; only C-library shims are owned.
    with pytest.raises(RuntimeError, match='owned preprocessor: header not found: ' + missing + '$'):
        preprocess('cpp', f'#include "{header}"\n', ROOT / 'projects' / PROJECTS['cpp'])


def test_python_extension_stops_at_the_missing_nanobind_closure():
    _, files = load_project('python')
    load_project('cpp')
    root = ROOT / 'projects' / PROJECTS['python']
    assert all(b'from _datasketches import' in files[f'datasketches/{name}.py']
               for name in ('__init__', 'PySerDe', 'TuplePolicy', 'KernelFunction'))
    with pytest.raises(RuntimeError, match='header not found: nanobind/nanobind.h$'):
        preprocess('python', files['src/datasketches.cpp'].decode(), root / 'src', [root / 'include'])
