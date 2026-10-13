"""Pure fake source/IR controls; no compiler, FFI or native execution."""

import re
import textwrap

import pytest

from tests import tar_attribute_strict_oracle as oracle
from tests.integration.test_worker_size_prior_closed_world import require_definition


class _Pool:
    def __init__(self):
        self.declarations = ''
        self.names = {}

    def name(self, value):
        if value not in self.names:
            name = f'@".literal.{len(self.names)}"'
            data = ''.join('\\' + format(byte, '02X') for byte in value.encode()) + '\\00'
            self.declarations += f'{name} = internal constant [{len(value.encode()) + 1} x i8] c"{data}"\n'
            self.names[value] = name
        return self.names[value]


def _function(symbol, body, *, method=False, adapter=False):
    result, args = ('ptr', 'ptr %self, ptr %intrinsic, ptr %expr') if method else ('void', '')
    if adapter:
        result, args = 'ptr', 'ptr %captures, ptr %args'
    return f'define external {result} @{symbol}({args}) {{\n{body}  ret {result}' + (
        ' null\n}\n' if result == 'ptr' else '\n}\n'
    )


def _source():
    diagnostics = oracle._MUTATION_DIAGNOSTICS
    body = ''.join(f'if intrinsic == {name!r}:\n    raise NotImplementedError({message!r})\n'
                   for name, message in zip(('open_file', 'rename_file', 'chmod_file'), diagnostics[:3]))
    body += textwrap.dedent(oracle._UTIME_BRANCH).strip() + '\n'
    body += f'if intrinsic == "sync_file":\n    raise NotImplementedError({diagnostics[4]!r})\n'
    body += f'raise NotImplementedError({diagnostics[5]!r})\n'
    dispatch = textwrap.dedent(oracle._WINDOWS_BRANCH).strip() + '\n'
    dispatch += 'raise NotImplementedError(f"unknown pcc.unsafe intrinsic {intrinsic!r}")\n'
    return ('class UnsafeIntrinsicMixin:\n'
            '    _UNSAFE_INTRINSICS = UNSAFE_INTRINSICS\n'
            '    def _is_unsafe_intrinsic(self, name: str) -> bool:\n'
            '        return name in self._UNSAFE_INTRINSICS\n'
            '    def _emit_unsafe_file_mutation(self, intrinsic: str, expr: Call) -> ir.Value:\n'
            + textwrap.indent(body, '        ')
            + '    def _emit_unsafe_intrinsic_call(self, intrinsic: str, expr: Call) -> ir.Value:\n'
            + textwrap.indent(dispatch, '        '))


def _method_ir(pool):
    body = '  br i1 true, label %work, label %bad\nwork:\n'
    callees = ('user_pcc_ir_ir_IRBuilder_call2', 'user_pcc_ir_ir_IRBuilder_sext',
               oracle._METHOD_PREFIX + '_unsafe_expect_arity', oracle._METHOD_PREFIX + '_unsafe_ptr_arg',
               oracle._METHOD_PREFIX + '_target_sys_platform_text',
               'user_pcc_frontends_python_codegen_ir_decl_helpers_IrDeclHelperMixin__declare_external_function')
    body += ''.join(f'  call ptr @{callee}()\n' for callee in callees)
    for literal in ('utime_file', 'utime', 'unsafe.utime_file.i32', 'unsafe.utime_file'):
        body += f'  call void @literal_use(ptr {pool.name(literal)})\n'
    for literal in oracle._MUTATION_DIAGNOSTICS:
        body += f'  call ptr @py_exc_new(i64 11, ptr {pool.name(literal)})\n'
    mutation = _function(oracle.MUTATION_METHOD, body, method=True)
    body = '  br i1 true, label %work, label %bad\nwork:\n'
    body += '  call ptr @py_exc_new_with_value(i64 11, ptr null)\n'
    for callee in (oracle.MUTATION_METHOD,
                   'user_pcc_frontends_python_codegen_windows_platform_handles',
                   'user_pcc_frontends_python_codegen_windows_platform_emit'):
        body += f'  call ptr @{callee}()\n'
    for literal in ('win32', 'unknown pcc.unsafe intrinsic '):
        body += f'  call void @literal_use(ptr {pool.name(literal)})\n'
    dispatch = _function(oracle.DISPATCH_METHOD, body, method=True)
    adapters = []
    bindings = ('  %capture_tuple = call ptr @py_tuple_new(i64 0)\n'
                '  store ptr %capture_tuple, ptr %captures\n'
                '  %signature_tuple = call ptr @py_tuple_new(i64 5)\n'
                '  store ptr %signature_tuple, ptr %signature\n'
                f'  call void @py_tuple_set_item(ptr %signature_tuple, i64 0, ptr {pool.name("__pcc_func_signature_v1__")})\n')
    for index, symbol in enumerate((oracle.MUTATION_METHOD, oracle.DISPATCH_METHOD)):
        adapter = symbol + '_method_native_adapter'
        adapters.append(_function(adapter, f'  call ptr @{symbol}()\n', adapter=True))
        name = pool.name(symbol[len(oracle._METHOD_PREFIX):])
        bindings += f'  %status{index} = call i64 @py_func_new_signature_slots(ptr %captures, ptr %signature, ptr @{adapter}, ptr {name}, ptr %output{index})\n'
        bindings += f'  %callable{index} = call ptr @pcc_gc_take_pinned_slot(ptr %output{index}, i64 0)\n'
        bindings += f'  store ptr %callable{index}, ptr %publication{index}\n'
        bindings += f'  %published_method{index} = load ptr, ptr %publication{index}\n'
        bindings += f'  call void @py_class_add_method(ptr %class, ptr {name}, ptr %published_method{index})\n'
        bindings += f'  call i64 @py_class_setattr_raw(ptr %class, ptr {name}, ptr %published_method{index})\n'
    return mutation + dispatch + ''.join(adapters), bindings


def _dynamic(pool, attribute, *, method=False, slots=False, suffix=''):
    name = pool.name(attribute)
    body = f'  %attribute{suffix} = getelementptr inbounds [1 x i8], ptr {name}, i32 0, i32 0\n'
    if method:
        body += f'  %callable{suffix} = call ptr @py_obj_load_method(ptr %owner, ptr %attribute{suffix}, ptr %self_slot)\n'
        body += f'  call ptr @py_obj_call_method(ptr %callable{suffix}, ptr %self, ptr %args)\n'
    else:
        body += f'  %callable{suffix} = call ptr @py_obj_getattr(ptr %owner, ptr %attribute{suffix})\n'
        if slots:
            body += f'  store ptr %callable{suffix}, ptr %root{suffix}\n'
            body += f'  call i64 @pcc_gc_root_copy_lease(ptr %copy{suffix}, ptr %root{suffix})\n'
            body += f'  call i64 @py_obj_call_slots(ptr %copy{suffix}, ptr %args, ptr %kwargs, ptr %out)\n'
        else:
            body += f'  call ptr @py_obj_call(ptr %callable{suffix}, ptr %args, ptr %kwargs)\n'
    return body


def _import(pool, module, table, index):
    module_name, table_name = pool.name(module), pool.name(table)
    return (f'  %module{index} = call ptr @py_compiled_module_import_by_name(ptr {module_name})\n'
            f'  store ptr %module{index}, ptr %module_root{index}\n'
            f'  %receiver{index} = call ptr @pcc_gc_load_ptr(ptr null, ptr %module_root{index})\n'
            f'  %member{index} = call ptr @py_obj_getattr(ptr %receiver{index}, ptr {table_name})\n'
            f'  store ptr %member{index}, ptr %member_root{index}\n'
            f'  call i64 @pcc_gc_root_copy_borrowed_lease(ptr %local{index}, ptr %member_root{index})\n'
            f'  %table{index} = load ptr, ptr %local{index}\n'
            f'  call void @py_dict_update(ptr %destination, ptr %table{index})\n')


def _fixture(name):
    pool = _Pool()
    tables = {
        oracle.TAR_COMPILER_MODULES[0]: (('_KERNEL32', 'py_set_from_iterable'),),
        oracle.CONSTANT_ONLY_MODULE: (('PLATFORM_MACHINE_ABIS', 'py_static_aggregate_build'),),
        oracle._UNSAFE: (('UNSAFE_INTRINSICS', 'py_set_new'), ('_UNSAFE_INTRINSIC_FAMILIES', 'py_static_aggregate_build')),
        oracle._WINDOWS: (('_SIGNATURES', 'py_static_aggregate_build'),),
        oracle.TAR_COMPILER_MODULES[5]: (('_UNSAFE_INTRINSIC_RETURN_TYPES', 'py_dict_new_presized'),),
    }
    declarations, initialization = '', ''
    for index, (table, constructor) in enumerate(tables.get(name, ())):
        slot = '.modvar.' + name.replace('.', '_') + '.' + table
        declarations += f'@"{slot}" = global ptr null\n'
        initialization += f'  %table{index} = call ptr @{constructor}()\n'
        initialization += f'  call void @pcc_gc_store_root(ptr @"{slot}", ptr %table{index})\n'
        initialization += f'  %published{index} = load ptr, ptr @"{slot}"\n'
        initialization += f'  call void @py_module_attr_set(ptr {pool.name(name)}, ptr {pool.name(table)}, ptr %published{index})\n'
    section = ''
    if name == oracle._UNSAFE:
        methods, bindings = _method_ir(pool)
        section += methods
        initialization += bindings
        registry = pool.name('_UNSAFE_INTRINSICS')
        slot = '.modvar.' + name.replace('.', '_') + '.UNSAFE_INTRINSICS'
        initialization += f'  %registry = load ptr, ptr @"{slot}"\n'
        initialization += f'  call i64 @py_class_setattr_raw(ptr %class, ptr {registry}, ptr %registry)\n'
        body = (f'  %registry = call ptr @py_obj_getattr(ptr %self, ptr {registry})\n'
                '  call i64 @py_obj_contains(ptr %registry, ptr %name)\n')
        section += _function(oracle._METHOD_PREFIX + '_is_unsafe_intrinsic', body)
    if tables.get(name):
        section += _function('_pcc_py_module_top_' + name.replace('.', '_'), initialization)
    consumers = {
        oracle.TAR_COMPILER_MODULES[0]: (('system_dll', '_KERNEL32'),),
        oracle._UNSAFE: (('_unsafe_intrinsic_family', '_UNSAFE_INTRINSIC_FAMILIES'),),
        oracle._WINDOWS: (('handles', '_SIGNATURES'), ('emit', '_SIGNATURES')),
        oracle.TAR_COMPILER_MODULES[5]: (('_unsafe_intrinsic_return_type', '_UNSAFE_INTRINSIC_RETURN_TYPES'),),
    }
    for function, table in consumers.get(name, ()):
        slot = '.modvar.' + name.replace('.', '_') + '.' + table
        body = f'  %value = load ptr, ptr @"{slot}"\n  call i64 @py_obj_contains(ptr %value, ptr %key)\n'
        if name == oracle._WINDOWS and function == 'emit':
            for literal in ('pcc_win_', 'builder'):
                body += f'  call void @literal_use(ptr {pool.name(literal)})\n'
            body += _dynamic(pool, '_declare_external_function', method=True, suffix='declare')
            body += f'  %builder = call ptr @py_obj_getattr(ptr %owner, ptr {pool.name("builder")})\n'
            body += _dynamic(pool, 'call', slots=True, suffix='call').replace('ptr %owner,', 'ptr %builder,')
        emitted = _function('user_' + name.replace('.', '_') + '_' + function, body,
                            method=name == oracle._WINDOWS and function == 'emit')
        if name == oracle._WINDOWS and function == 'emit':
            emitted = emitted.replace('ptr %self, ptr %intrinsic, ptr %expr', 'ptr %owner, ptr %intrinsic, ptr %expr')
        section += emitted
    remaining = {}
    if name in (oracle.CONSTANT_ONLY_MODULE, oracle._FREESTANDING):
        consumer_pool = _Pool()
        body = _import(consumer_pool, oracle.CONSTANT_ONLY_MODULE, 'PLATFORM_MACHINE_ABIS', 0)
        body += _import(consumer_pool, oracle._WINDOWS, '_SIGNATURES', 1)
        for literal in ('utime_file', 'utime'):
            body += f'  call void @literal_use(ptr {consumer_pool.name(literal)})\n'
        consumer = consumer_pool.declarations + _function('user_' + oracle._FREESTANDING.replace('.', '_')
                                                         + '_freestanding_allowed_external_symbols', body)
        if name == oracle._FREESTANDING:
            section = consumer
        else:
            remaining[oracle._FREESTANDING] = consumer
    return declarations + pool.declarations + section, remaining


@pytest.mark.parametrize('name', oracle.TAR_COMPILER_MODULES)
def test_six_strict_compiler_modules_require_real_tables_and_consumers(name):
    section, remaining = _fixture(name)
    required = oracle.require_tar_attribute_module(name, _source(), section, remaining, require_definition)
    assert bool(required) == (name != oracle._FREESTANDING)
    if name == oracle._UNSAFE:
        assert oracle.MUTATION_METHOD in required and oracle.DISPATCH_METHOD in required
        assert len(required) == 6


@pytest.mark.parametrize('name', (
    oracle.TAR_COMPILER_MODULES[0], oracle.CONSTANT_ONLY_MODULE, oracle._UNSAFE,
    oracle._WINDOWS, oracle.TAR_COMPILER_MODULES[5],
))
@pytest.mark.parametrize('damage', ('constructor', 'publication', 'consumer', 'initializer', 'detached_value'))
def test_table_oracle_rejects_dormant_or_unbound_tables(name, damage):
    section, remaining = _fixture(name)
    if damage == 'constructor':
        for call in ('py_dict_new_presized', 'py_set_from_iterable', 'py_set_new', 'py_static_aggregate_build'):
            section = section.replace('@' + call + '(', '@unowned_constructor(')
    elif damage == 'publication':
        section = section.replace('@py_module_attr_set(', '@not_a_publication(')
    elif damage == 'initializer':
        section = section.replace('define external void @_pcc_py_module_top_', 'define external void @unrelated_initializer_')
    elif damage == 'detached_value':
        section = section.replace(', ptr %table0)', ', ptr %unrelated)')
    elif name == oracle.CONSTANT_ONLY_MODULE:
        remaining[oracle._FREESTANDING] = remaining[oracle._FREESTANDING].replace('ptr %receiver0,', 'ptr %unrelated,')
    else:
        section = section.replace('ptr %value, ptr %key', 'ptr %unrelated, ptr %key')
    with pytest.raises(AssertionError):
        oracle.require_tar_attribute_module(name, _source(), section, remaining, require_definition)


@pytest.mark.parametrize('old,new', (
    ('("darwin", "linux")', '("darwin", "win32")'),
    ('self._unsafe_expect_arity(intrinsic, expr, 2)', 'self._unsafe_expect_arity(intrinsic, expr, 1)'),
    ('[_CSTR, _CSTR]', '[_CSTR]'),
    ('self.builder.sext(raw, _I64', 'self.builder.zext(raw, _I64'),
    ('== "win32"', '== "linux"'),
    ('return emit(self, intrinsic, expr)', 'return None'),
    ('unknown pcc.unsafe intrinsic', 'silently unsupported'),
    ('if intrinsic == "utime_file":', 'raise NotImplementedError(intrinsic)\n        if intrinsic == "utime_file":'),
))
def test_method_source_oracle_rejects_changed_supported_or_diagnostic_contract(old, new):
    with pytest.raises(AssertionError):
        oracle._method_source(_source().replace(old, new))


@pytest.mark.parametrize('damage', (
    'call2', 'sext', 'windows_emit', 'signature', 'literal', 'binding', 'diagnostic_count', 'adapter',
    'py_cpy', 'strict_stub', 'extra_dynamic_diagnostic', 'adapter_signature', 'factory_argument',
    'class_setter', 'class_value', 'callable_value', 'signature_value', 'capture_value',
))
def test_method_ir_oracle_rejects_stub_signature_binding_and_callee_replacements(damage):
    pool = _Pool()
    section, binding = _method_ir(pool)
    section = pool.declarations + section
    replacements = {
        'call2': ('@user_pcc_ir_ir_IRBuilder_call2(', '@unrelated('),
        'sext': ('@user_pcc_ir_ir_IRBuilder_sext(', '@unrelated('),
        'windows_emit': ('@user_pcc_frontends_python_codegen_windows_platform_emit(', '@unrelated('),
        'signature': ('ptr %self, ptr %intrinsic, ptr %expr', 'i64 %self, ptr %intrinsic, ptr %expr'),
        'literal': ('call void @literal_use(ptr ' + pool.name('utime_file') + ')', 'call void @unrelated()'),
        'diagnostic_count': ('@py_exc_new(i64 11,', '@py_exc_new(i64 3,'),
        'adapter': ('call ptr @' + oracle.MUTATION_METHOD + '()', 'call ptr @unrelated()'),
        'py_cpy': ('@user_pcc_ir_ir_IRBuilder_call2(', '@py_cpy_call('),
        'strict_stub': ('@user_pcc_ir_ir_IRBuilder_call2(', '@strict.nolib.stub('),
        'extra_dynamic_diagnostic': ('work:\n', 'work:\n  call ptr @py_exc_new_with_value(i64 11, ptr null)\n'),
        'adapter_signature': ('ptr %captures, ptr %args', 'ptr %captures'),
    }
    if damage == 'binding':
        binding = binding.replace('@py_func_new_signature_slots(', '@unrelated(')
    elif damage == 'factory_argument':
        binding = binding.replace('ptr %signature, ptr @', 'ptr %signature, ptr %wrong, ptr @')
    elif damage == 'signature_value':
        binding = binding.replace('store ptr %signature_tuple,', 'store ptr %wrong,')
    elif damage == 'capture_value':
        binding = binding.replace('store ptr %capture_tuple,', 'store ptr %wrong,')
    elif damage == 'class_setter':
        binding = binding.replace('@py_class_add_method(', '@unrelated(')
    elif damage == 'class_value':
        binding = binding.replace('@py_class_add_method(ptr %class,', '@py_class_add_method(ptr %other_class,')
    elif damage == 'callable_value':
        binding = binding.replace(', ptr %published_method0)', ', ptr %unrelated)')
    else:
        old, new = replacements[damage]
        section = section.replace(old, new, 1)
    with pytest.raises(AssertionError):
        oracle.require_tar_unsafe_methods(_source(), section, binding, require_definition)


@pytest.mark.parametrize('route', ('direct', 'method', 'slots'))
@pytest.mark.parametrize('damage', ('none', 'wrong_attribute', 'detached_callable', 'detached_receiver'))
def test_dynamic_calls_require_the_named_attribute_callable_and_receiver(route, damage):
    pool = _Pool()
    body = _dynamic(pool, 'call', method=route == 'method', slots=route == 'slots')
    pool.name('other')
    if damage == 'wrong_attribute':
        body = body.replace(pool.name('call'), pool.name('other'))
    elif damage == 'detached_callable':
        body = body.replace('ptr %callable,', 'ptr %unrelated,')
        body = body.replace('store ptr %callable,', 'store ptr %unrelated,')
    elif damage == 'detached_receiver':
        body = body.replace('ptr %owner,', 'ptr %unrelated,')
    if damage == 'none':
        oracle._dynamic_attribute_call(pool.declarations, body, 'call', receiver={'%owner'})
    else:
        with pytest.raises(AssertionError):
            oracle._dynamic_attribute_call(pool.declarations, body, 'call', receiver={'%owner'})


def test_new_oracle_does_not_change_previous_module_requirements():
    assert oracle.require_tar_attribute_module('pcc.backend.elf_x86_64', '', '', {}, require_definition) == []


def test_original_symbol_oracle_still_rejects_notimplemented_stub():
    with pytest.raises(AssertionError):
        require_definition(_function('old_symbol', '  call ptr @py_exc_new(i64 11, ptr null)\n'), 'old_symbol')


def _current_printer(section, *, holder='constant', alias='bitcast', retained='%value'):
    """Rewrite a fixture into the shapes the current IR printer emits.

    Calls carry their function type, module roots are stored through a
    bitcast slot, consumers retain the loaded table before using it and
    literal objects reach their users through constant static item arrays.
    """
    count = iter(range(1_000_000))

    def store(match):
        index = next(count)
        cast = (f'bitcast ptr {match[1]} to ptr' if alias == 'bitcast'
                else f'getelementptr inbounds ptr, ptr {match[1]}, i64 1')
        return (f'%root.slot{index} = {cast}\n'
                f'  call void @pcc_gc_store_root(ptr %root.slot{index}, ptr {match[2]})')

    section = re.sub(r'call void @pcc_gc_store_root\(ptr (@"[^"]+"), ptr (%\w+)\)', store, section)
    section = section.replace(
        'call i64 @py_obj_contains(ptr %value, ptr %key)',
        f'%retained = call ptr @pcc_gc_retain(ptr {retained})\n'
        '  call i64 @py_obj_contains(ptr %retained, ptr %key)')
    items = []

    def literal(match):
        index = next(count)
        items.append(f'@".static.items.{index}" = internal {holder} [1 x ptr] [ptr bitcast (ptr {match[1]} to ptr)]\n')
        return f'%items{index} = getelementptr inbounds [1 x ptr], ptr @".static.items.{index}", i32 0, i32 0'

    section = re.sub(r'call void @literal_use\(ptr (@"\.literal\.\d+")\)', literal, section)
    section = re.sub(r'\bcall (\w+) @', r'call \1 (ptr) @', section)
    return ''.join(items) + section


def _current_fixture(name, **shape):
    section, remaining = _fixture(name)
    return (_current_printer(section, **shape),
            {key: _current_printer(value, **shape) for key, value in remaining.items()})


@pytest.mark.parametrize('name', oracle.TAR_COMPILER_MODULES)
def test_strict_modules_accept_the_current_printer_shapes(name):
    section, remaining = _current_fixture(name)
    assert '@pcc_gc_retain(' in section or name in (oracle.CONSTANT_ONLY_MODULE, oracle._FREESTANDING)
    required = oracle.require_tar_attribute_module(name, _source(), section, remaining, require_definition)
    assert bool(required) == (name != oracle._FREESTANDING)


@pytest.mark.parametrize('name,damage', (
    (oracle._FREESTANDING, 'mutable_holder'),
    (oracle._UNSAFE, 'mutable_holder'),
    (oracle.CONSTANT_ONLY_MODULE, 'offset_alias'),
    (oracle.TAR_COMPILER_MODULES[5], 'offset_alias'),
    (oracle.TAR_COMPILER_MODULES[0], 'retained_other'),
    (oracle._WINDOWS, 'retained_other'),
))
def test_current_printer_shapes_still_reject_unbound_tables_and_literals(name, damage):
    shape = {'mutable_holder': {'holder': 'global'}, 'offset_alias': {'alias': 'offset'},
             'retained_other': {'retained': '%unrelated'}}[damage]
    section, remaining = _current_fixture(name, **shape)
    with pytest.raises(AssertionError):
        oracle.require_tar_attribute_module(name, _source(), section, remaining, require_definition)
