"""Opt-in finite owned-optimizer native semantic verification (Linux x86-64).

Run under a process-tree supervisor with a 1200-second/2-GiB compile envelope:
    uv run python scripts/verify_owned_optimizer_native.py \
        --runtime-archive /absolute/source-matched/libpy_runtime_pcc_py.a \
        --output-dir /absolute/fresh/output

The caller supplies process-tree resource supervision; this script is not a
replacement watchdog. It holds build/.pcc-performance.lock, never provisions a
runtime, discovers no closure, uses only owned object/link implementations, and
runs the resulting fixture once with a 10-second process-group deadline.
Application IR passes are explicitly OFF; this is not a default-pass performance,
pcc1, full-bootstrap, five-GC or generic CPython-interop qualification.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import ast
import fcntl
import gc
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CONSUMER = 'pcc.frontends.python.compiled_owned_passes'
ENTRY = 'owned_optimizer_native_entry'
TARGET = 'x86_64-unknown-linux-gnu'
MODULES = (
    'pcc.ir.optimization.mem2reg',
    'pcc.ir.optimization.instsimplify',
    'pcc.ir.optimization.simplifycfg',
    'pcc.ir.optimization.foldselect',
    'pcc.ir.optimization.instcombine',
    'pcc.ir.optimization.dce',
    'pcc.ir.optimization.inline',
    'pcc.frontends.python.compiled_default_passes',
    'pcc.ir.optimization.ir_mutator',
    'pcc.ir.optimization.text_tokens',
    'pcc.ir.optimization.integer_fold_contract',
    'pcc.frontends.python.pipeline_pass_config',
    'pcc.driver.paths',
    CONSUMER, ENTRY,
)
EXPECTED_OUTPUT = (
    'mem2reg', 'sroa', 'instsimplify', 'simplifycfg', 'fold-two-entry-phi',
    'instcombine', 'dce', 'inline-default-and-defined',
    'literal-defaults-and-bool-keywords', 'scalar-helpers-and-strict-bool',
    'native-keyword-arity-errors', 'live-rebinding-and-one-replacement-call',
    'owned-optimizer-native-ok',
)


def _forbidden(*args, **kwargs):
    raise AssertionError('finite verifier forbids provisioning, discovery and external build tools')


def _signature(info):
    from pcc.frontends.python.pipeline_exports import _export_default_to_wire
    result = dict(info)
    result['param_types'] = tuple(result['param_types'])
    result['call_sig'] = tuple({**arg, 'default': _export_default_to_wire(arg['default'])}
                               for arg in result['call_sig'])
    return result


def _check_initializers(function, siblings):
    from pcc.backend.self_backend_parse import _parse_instruction
    definitions, actual = {}, []
    imported = False
    for block in function.blocks:
        for instruction in block.instructions:
            if instruction.opcode not in ('bitcast', 'call'):
                continue
            parsed = _parse_instruction(function.name, block.name, instruction.text.strip())
            if parsed.kind == 'cast':
                operation, result, _source_type, source, _dest_type = parsed.data
                if operation == 'bitcast':
                    definitions[result] = source
            elif parsed.kind == 'call':
                _result, _type, callee, indirect, arguments, *_rest = parsed.data
                if not indirect and callee == 'py_compiled_module_register_init':
                    assert not imported and len(arguments) == 2
                    actual.append(definitions[arguments[1][1]])
                elif not indirect and callee == 'py_compiled_module_import_by_name':
                    imported = True
    assert actual == ['@_pcc_py_module_top_' + name.replace('.', '_') for name in siblings]


def _compile(output, archive):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.owned_elf_link import link_inputs
    from pcc.backend.self_backend_target_passes import (
        resolve_self_target_pass_names, resolve_self_target_pass_transport,
    )
    from pcc.frontends.python import pipeline, pipeline_context as context
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.codegen.layer1_support import _default_native_module_exports
    from pcc.frontends.python.type_infer import infer_module
    from pcc.ir.optimization.ir_mutator import MutableModule

    transport = resolve_self_target_pass_transport()
    assert transport == 'text' and resolve_self_target_pass_names(transport=transport) == ()
    paths = [ROOT / (name.replace('.', '/') + '.py') for name in MODULES[:-1]]
    paths.append(ROOT / 'tests/python/fixtures/owned_optimizer_native.py')
    siblings = pipeline._order_module_inits([str(path) for path in paths], list(MODULES), ENTRY)
    assert set(siblings) == set(MODULES) - {ENTRY}
    parsed, exports, derived = context.build_closed_world_context([str(path) for path in paths], list(MODULES))
    selected = [(node.module, alias.name) for node in ast.parse(paths[-2].read_text()).body
                if isinstance(node, ast.ImportFrom) for alias in node.names]
    assert len(selected) == 11
    static = _default_native_module_exports(CONSUMER)
    for owner, name in selected:
        actual, declared = _signature(exports[owner][name]), _signature(static[owner][name])
        assert all(actual[key] == value for key, value in declared.items()), (owner, name)
    objects = []
    for module in siblings + [ENTRY]:
        index = MODULES.index(module)
        lifted, path = parsed[index], paths[index]
        external = {owner: members for owner, members in exports.items() if owner != module}
        typed = (infer_module(lifted) if module == CONSUMER else infer_module(
            lifted, external_exports=external, derived_class_map=derived,
            contextual_host_params=context._contextual_host_params_for_module(lifted, module)))
        cg = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode='on')
        cg._strict_no_libpython = cg._prefer_native_callable_values = True
        cg._module_source_path, cg._target_triple, cg.module.triple = str(path), TARGET, TARGET
        cg._skip_program_main, cg._sibling_module_inits = module != ENTRY, siblings
        table = context._copy_native_module_exports(cg._native_module_exports) if context._module_uses_default_native_exports(module) else {}
        if module != CONSUMER:
            table.update(external)
        cg._native_module_exports = table
        cg._native_function_object_exports = context._closed_world_function_object_exports(exports, module)
        cg._native_boxed_int_functions = context._closed_world_boxed_int_functions(exports, module)
        text = str(cg.generate(typed))
        assert pipeline._apply_python_ir_pass_pipeline(text, module_name=module, default_raw='off', strict_no_libpython=True) == text
        ir_path = output / (module.replace('.', '_') + '.ll')
        ir_path.write_text(text)
        inventory = MutableModule.parse(text)
        assert len({fn.name for fn in inventory.functions}) == len(inventory.functions)
        for fn in inventory.functions:
            assert fn.blocks and all(block.terminator and block.terminator.is_terminator() for block in fn.blocks)
        prefix = 'user_' + module.replace('.', '_') + '_'
        expected = []
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.FunctionDef):
                expected.append(prefix + node.name)
            elif isinstance(node, ast.ClassDef):
                expected.extend(prefix + node.name + '_' + child.name for child in node.body if isinstance(child, ast.FunctionDef))
        expected.extend(prefix + name + '_native_adapter' for owner, name in selected if owner == module)
        expected.append('main' if module == ENTRY else '_pcc_py_module_top_' + module.replace('.', '_'))
        assert all(inventory.function(name) is not None for name in expected), module
        if module == ENTRY:
            _check_initializers(inventory.function('main'), siblings)
        decoded = re.sub(r'\\([0-9A-Fa-f]{2})', lambda match: chr(int(match[1], 16)), text)
        assert 'no-libpython function unavailable:' not in decoded and 'unsupported import' not in decoded
        assert 'strict.nolib.stub' not in text and not re.search(r'\bcall [^\n]*@py_cpy_', text)
        pipeline._check_compiled_module_imports_are_in_closure(module, text, list(MODULES))
        inventory, decoded, cg, typed = None, '', None, None
        gc.collect()
        object_path = output / (module.replace('.', '_') + '.o')
        object_path.write_bytes(emit_owned_object(text, TARGET))
        objects.append(str(object_path))
        text = ''
        gc.collect()
    executable = output / 'owned_optimizer_fixture'
    # The production wrapper supplies its two owned Linux thread ABI fragments.
    # Calling raw elf_x86_64.link_static_executable here omits those dependencies.
    link_inputs(target=TARGET, output=str(executable), objects=objects,
                archives=[str(archive)], map_path=str(output / 'owned_optimizer_fixture.map'))
    return executable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-archive', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if not sys.platform.startswith('linux') or os.uname().machine != 'x86_64':
        parser.error('this explicit native fixture requires Linux x86-64')
    for name in ('PCC_PY_DEBUG_INFO', 'PCC_SELF_TARGET_PASSES', 'PCC_SELF_TARGET_PASS_TRANSPORT', 'PCC_PYTHON_IR_PASS_SKIP_MODULES'):
        if os.environ.get(name):
            parser.error('unexpected override: ' + name)
    archive, output = args.runtime_archive.resolve(), args.output_dir.resolve()
    os.environ.update(PCC_SOURCE_ROOT=str(ROOT), PCC_RUNTIME_ARCHIVE=str(archive),
                      PCC_TEST_NO_NATIVE_PROVISIONING='1', PCC_PYTHON_IR_PASSES='off',
                      PCC_WITH_THREADS='1', PCC_REFCOUNT_KIND='atomic', PCC_NO_AUTO_PCC1='1', PCC_GC_BACKEND='0',
                      PCC_SELF_BACKEND_OBJECT_CACHE='0', PCC_PY_FRONTEND_IR_CACHE='0')
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive
    from tests.python.process_timeout import run_process_group_timeout
    from pcc.frontends.python import pipeline
    _verified_test_runtime_archive(str(archive), threads=True)
    output.mkdir(parents=True, exist_ok=False)
    (ROOT / 'build').mkdir(exist_ok=True)
    with open(ROOT / 'build/.pcc-performance.lock', 'a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with ExitStack() as guards:
            guards.enter_context(patch('subprocess.Popen', _forbidden))
            for name in ('system', 'fork', 'posix_spawn', 'posix_spawnp'):
                if hasattr(os, name):
                    guards.enter_context(patch.object(os, name, _forbidden))
            guards.enter_context(patch.object(pipeline, '_prepare_single_source_compile_closure', _forbidden))
            guards.enter_context(patch.object(pipeline, '_prepare_multi_source_compile_closure', _forbidden))
            executable = _compile(output, archive)
        result = run_process_group_timeout([str(executable)], timeout=10, cwd=output,
                                          stderr_path=output / 'native.stderr')
        (output / 'native.stdout').write_text(result.stdout)
        assert result.returncode == 0, (result.returncode, result.stderr)
        assert tuple(result.stdout.splitlines()) == EXPECTED_OUTPUT
        assert not result.stderr, result.stderr
        _verified_test_runtime_archive(str(archive), threads=True)
    print('PASS: finite fifteen-source owned native optimizer semantics')


if __name__ == '__main__':
    main()
