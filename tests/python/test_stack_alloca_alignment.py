"""Word-aligned descending allocas retain nominal spans and precise guards."""
import pytest
from pcc.backend import BackendUnavailable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_prepare import prepare_module_for_target
from pcc.backend.self_backend_kernel import get_indexed_function_kernel

TARGETS = ('arm64-apple-darwin', 'aarch64-unknown-linux-gnu',
           'x86_64-unknown-linux-gnu', 'x86_64-pc-windows-msvc')


def buffer_function(prefix, size):
    args = 'i32 %d' if prefix == 4 else 'i64 %a, i64 %b, i64 %c, i32 %d'
    extra = '  %wide = zext i32 %d to i64\n'
    if prefix == 28:
        extra += '  %p0 = or i64 %a, %b\n  %p1 = or i64 %p0, %c\n  %padding = or i64 %p1, %wide\n'
    else:
        extra += '  %padding = or i64 %wide, 0\n'
    return f'''define i64 @buffer_{prefix}_{size}({args}) {{
entry:
  %buffer = alloca [{size} x i8]
  %address = ptrtoint ptr %buffer to i64
  %alignment = and i64 %address, 7
  store i8 42, ptr %buffer, align 1
  %tail = getelementptr i8, ptr %buffer, i64 {size - 1}
  store i8 43, ptr %tail, align 1
  %last = load i8, ptr %tail, align 1
  %last_bad = xor i8 %last, 43
  %bad = zext i8 %last_bad to i64
{extra}  %status0 = or i64 %alignment, %bad
  %status = or i64 %status0, %padding
  ret i64 %status
}}
'''


def root_function(size=32, origin=0):
    initializes = '\n'.join(f'  %s{i} = getelementptr i8, ptr %slots, i64 {i * 8}\n  store ptr null, ptr %s{i}, align 1' for i in range(4))
    return f'''@root_map = private constant i32 4
declare void @pcc_gc_frame_enter(ptr, ptr)
declare void @pcc_gc_frame_leave(ptr)
define i64 @four_roots(i32 %prefix) {{
entry:
  %buffer = alloca [{size} x i8]
  %slots = getelementptr i8, ptr %buffer, i64 {origin}
{initializes}
  call void @pcc_gc_frame_enter(ptr @root_map, ptr %slots)
  call void @pcc_gc_frame_leave(ptr %slots)
  %wide = zext i32 %prefix to i64
  ret i64 %wide
}}
'''


def module_text(target=TARGETS[0], size=32, origin=0):
    return ('target triple = "' + target + '"\n'
            + ''.join(buffer_function(prefix, count) for prefix in (4, 28) for count in (1, 9, 17))
            + root_function(size, origin))


def prepare(text, legacy):
    return prepare_module_for_target(text, aggregate_returned_indirect=lambda _: False,
                                     materialize_legacy_slots=legacy)


@pytest.mark.parametrize('legacy', [False, True], ids=['indexed', 'legacy'])
@pytest.mark.parametrize('prefix', [4, 28])
@pytest.mark.parametrize('size', [1, 9, 17])
def test_odd_alloca_alignment_and_nominal_span(prefix, size, legacy):
    function = prepare('target triple = "arm64-apple-darwin"\n' + buffer_function(prefix, size), legacy).functions[0]
    kernel = get_indexed_function_kernel(function)
    ref = kernel.value_id('buffer')
    offset = kernel.alloca_offset(ref)
    span = kernel.type_span(kernel.alloca_type_id(ref))
    assert offset == ((prefix + size + 7) // 8) * 8
    assert offset % 8 == 0 and span.third == size and span.fourth == 1
    assert -offset + size <= -prefix
    assert function.frame_size >= offset and function.frame_size % 16 == 0


def test_indexed_and_legacy_layouts_match():
    results = []
    for legacy in (False, True):
        rows = []
        for function in prepare(module_text(), legacy).functions:
            kernel = get_indexed_function_kernel(function)
            rows.append((function.name, function.frame_size,
                         [(kernel.value_name(i), kernel.alloca_offset(i), kernel.type_span(kernel.alloca_type_id(i)).third)
                          for i in range(len(kernel.value_names)) if kernel.alloca_type_id(i) >= 0]))
        results.append(rows)
    assert results[0] == results[1]


# The Win64 object route has a separately recorded existing SEH-label blocker;
# this regression qualifies the Mac/ELF emitters without hiding that red gate.
@pytest.mark.parametrize('target', TARGETS[:3])
def test_program_emits_owned_target_object(tmp_path, target):
    data = emit_owned_object(module_text(target), target)
    assert len(data) > 128
    (tmp_path / 'alignment.o').write_bytes(data)


@pytest.mark.parametrize('size,origin,message', [
    (40, 4, 'managed slot is outside the final frame'),
    (31, 0, 'managed slot range exceeds its alloca'),
])
def test_padding_does_not_relax_root_guard(size, origin, message):
    with pytest.raises(BackendUnavailable, match=message):
        emit_owned_object(module_text(size=size, origin=origin), TARGETS[0])
