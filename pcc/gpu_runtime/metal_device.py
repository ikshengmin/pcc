"""Metal device runtime in freestanding pcc-Python.

The Objective-C runtime and the Metal framework are opened through the
dynamic loader, and every message is a typed C ABI call to ``objc_msgSend``.
There is no Objective-C source, no prebuilt bridge and no external compiler.
Device code is Metal Shading Language text compiled by the operating system
(``newLibraryWithSource:options:error:``).

The language version is always passed explicitly. With no compile options
the default depends on the executable: the simdgroup kernels were accepted from
a clang-built bridge and rejected from a program linked by the owned linker.

C ABI, all exported as ``pcc_metal_device_*``:

  open(context_out) -> status
  close(context) -> status
  set_language_version(context, version) -> status
  name(context, out, capacity) -> bytes copied
  buffer_create(context, nbytes, buffer_out) -> status
  buffer_contents(context, buffer) -> address
  buffer_length(context, buffer) -> bytes
  buffer_release(context, buffer) -> status
  pipeline_create(context, source, function, pipeline_out,
                  diagnostic, diagnostic_capacity) -> status
  pipeline_limits(context, pipeline, limits_out) -> status
  pipeline_release(context, pipeline) -> status
  dispatch_sync(context, pipeline, bindings, binding_count,
                grid, threads) -> status

``bindings`` is ``binding_count`` records of ``BINDING_BYTES`` bytes; ``grid``
and ``threads`` are three i64 words each (x, y, z). ``dispatch_sync`` returns
only after the command buffer completed, so every binding may be released as
soon as it returns.

A launch is refused before anything is encoded when the packet is malformed or
when the threadgroup is larger than the pipeline allows. Metal does not execute
such a dispatch and reports no error, so accepting it would return stale
output as a result.

A complete launch can also travel as one self-describing byte packet:

  packet_result_bytes(packet, packet_bytes) -> result size, 0 if malformed
  launch_packet(packet, packet_bytes, result, capacity) -> bytes written
  packet_main() -> process status; packet on fd 0, result on fd 1

``packet_main`` is a process entry point, so one object file is also a
complete launcher executable. The packet and result layouts are the
``PACKET_*`` and ``RESULT_*`` constants below.

Storage is shared (CPU-visible). Buffers and pipelines are owned by the caller
and are released explicitly; this module keeps no table of them.
"""

from pcc import i64
from pcc.extern import c_abi_export
from pcc.unsafe import (
    call_c_abi,
    calloc,
    cstr,
    dynamic_library_close,
    dynamic_library_open,
    dynamic_library_symbol,
    free,
    load_i8,
    load_i64,
    load_ptr,
    logical_shift_left_i64,
    memcpy,
    memset,
    null,
    ptr_add,
    ptr_is_null,
    read,
    realloc,
    stack_alloc,
    store_i8,
    store_i64,
    store_ptr,
    write,
)


__pcc_freestanding__ = True


STATUS_OK = 0
STATUS_BAD_ARGUMENT = 1
STATUS_OUT_OF_MEMORY = 2
STATUS_OBJC_RUNTIME_UNAVAILABLE = 10
STATUS_METAL_FRAMEWORK_UNAVAILABLE = 11
STATUS_SYMBOL_UNAVAILABLE = 12
STATUS_DEVICE_UNAVAILABLE = 13
STATUS_QUEUE_UNAVAILABLE = 14
STATUS_SOURCE_REJECTED = 20
STATUS_FUNCTION_NOT_FOUND = 21
STATUS_PIPELINE_REJECTED = 22
STATUS_BUFFER_UNAVAILABLE = 30
STATUS_BAD_BINDING = 40
STATUS_DISPATCH_EXCEEDS_PIPELINE = 41
STATUS_ENCODER_UNAVAILABLE = 42
STATUS_COMMAND_FAILED = 43

# Device context: one calloc'd record owned by open()/close().
CONTEXT_OBJC = 0
CONTEXT_METAL = 8
CONTEXT_SEND = 16
CONTEXT_REGISTER = 24
CONTEXT_POOL_PUSH = 32
CONTEXT_POOL_POP = 40
CONTEXT_STRING_CLASS = 48
CONTEXT_DEVICE = 56
CONTEXT_QUEUE = 64
CONTEXT_OPTIONS_CLASS = 72
CONTEXT_LANGUAGE_VERSION = 80
CONTEXT_BYTES = 88

# One launch binding. A zeroed record is invalid: both kinds are nonzero.
BINDING_KIND = 0
BINDING_INDEX = 8
BINDING_POINTER = 16
BINDING_EXTENT = 24
BINDING_BYTES = 32
BINDING_KIND_BUFFER = 1
BINDING_KIND_BYTES = 2
# Metal's buffer argument table has 31 entries.
MAX_BINDING_INDEX = 30

# pipeline_limits() output record.
LIMIT_MAX_THREADS = 0
LIMIT_EXECUTION_WIDTH = 8
LIMIT_STATIC_MEMORY = 16
LIMITS_BYTES = 24

# grid / threads records.
EXTENT_X = 0
EXTENT_Y = 8
EXTENT_Z = 16

# Launch packet: a header, ``binding count`` descriptors, the NUL-terminated
# Metal source, the NUL-terminated entry name, then each binding's bytes in
# descriptor order. Every variable-length region is padded to eight bytes.
PACKET_MAGIC = 3553424030952538960
PACKET_MAGIC_AT = 0
PACKET_SOURCE_BYTES = 8
PACKET_FUNCTION_BYTES = 16
PACKET_BINDING_COUNT = 24
PACKET_GRID = 32
PACKET_THREADS = 56
# Zero selects DEFAULT_LANGUAGE_VERSION.
PACKET_LANGUAGE_VERSION = 80
PACKET_HEADER_BYTES = 88
PACKET_BINDING_KIND = 0
PACKET_BINDING_INDEX = 8
PACKET_BINDING_NBYTES = 16
PACKET_BINDING_READBACK = 24
PACKET_BINDING_BYTES = 32
PACKET_MAX_BYTES = 1073741824

# Launch result: a header, a fixed diagnostic text region, then one record per
# read-back buffer: its binding index, its byte count, its bytes padded to
# eight. Records are present only when RESULT_STAGE is STAGE_DONE.
RESULT_MAGIC = 3553986980905960272
RESULT_MAGIC_AT = 0
RESULT_STAGE = 8
RESULT_STATUS = 16
RESULT_MAX_THREADS = 24
RESULT_EXECUTION_WIDTH = 32
RESULT_READBACK_COUNT = 40
RESULT_LANGUAGE_VERSION = 48
RESULT_HEADER_BYTES = 56
RESULT_DIAGNOSTIC_BYTES = 512
RESULT_FIXED_BYTES = 568
RESULT_RECORD_INDEX = 0
RESULT_RECORD_NBYTES = 8
RESULT_RECORD_HEADER_BYTES = 16

# The stage that produced RESULT_STATUS.
STAGE_DONE = 0
STAGE_PACKET = 1
STAGE_OPEN = 2
STAGE_PIPELINE = 3
STAGE_BUFFER = 4
STAGE_DISPATCH = 5

# packet_main() process statuses.
PROCESS_OK = 0
PROCESS_INPUT_FAILED = 2
PROCESS_OUTPUT_FAILED = 3

# MTLLanguageVersion values are (major << 16) + minor. Version 3.0 is the
# default; a caller or a packet may name another one.
LANGUAGE_VERSION_3_0 = 196608
DEFAULT_LANGUAGE_VERSION = 196608

# MTLResourceStorageModeShared with the default CPU cache mode.
STORAGE_MODE_SHARED = 0
# MTLCommandBufferStatusCompleted.
COMMAND_BUFFER_COMPLETED = 4


@c_abi_export("pcc_metal_device_objc_selector")
def _selector(context, name):
    out = stack_alloc(8)
    call_c_abi(load_ptr(context, CONTEXT_REGISTER), "ptr", ("ptr",), (name,), out)
    return load_ptr(out, 0)


@c_abi_export("pcc_metal_device_objc_send")
def _send(context, target, name):
    out = stack_alloc(8)
    call_c_abi(
        load_ptr(context, CONTEXT_SEND), "ptr", ("ptr", "ptr"),
        (target, _selector(context, name)), out,
    )
    return load_ptr(out, 0)


@c_abi_export("pcc_metal_device_objc_send1")
def _send1(context, target, name, arg0):
    out = stack_alloc(8)
    call_c_abi(
        load_ptr(context, CONTEXT_SEND), "ptr", ("ptr", "ptr", "ptr"),
        (target, _selector(context, name), arg0), out,
    )
    return load_ptr(out, 0)


@c_abi_export("pcc_metal_device_objc_send2")
def _send2(context, target, name, arg0, arg1):
    out = stack_alloc(8)
    call_c_abi(
        load_ptr(context, CONTEXT_SEND), "ptr", ("ptr", "ptr", "ptr", "ptr"),
        (target, _selector(context, name), arg0, arg1), out,
    )
    return load_ptr(out, 0)


@c_abi_export("pcc_metal_device_objc_send3")
def _send3(context, target, name, arg0, arg1, arg2):
    out = stack_alloc(8)
    call_c_abi(
        load_ptr(context, CONTEXT_SEND), "ptr",
        ("ptr", "ptr", "ptr", "ptr", "ptr"),
        (target, _selector(context, name), arg0, arg1, arg2), out,
    )
    return load_ptr(out, 0)


@c_abi_export("pcc_metal_device_objc_send_word")
def _send_word(context, target, name) -> i64:
    out = stack_alloc(8)
    call_c_abi(
        load_ptr(context, CONTEXT_SEND), "i64", ("ptr", "ptr"),
        (target, _selector(context, name)), out,
    )
    return load_i64(out, 0)


@c_abi_export("pcc_metal_device_objc_send_void")
def _send_void(context, target, name) -> i64:
    call_c_abi(
        load_ptr(context, CONTEXT_SEND), "void", ("ptr", "ptr"),
        (target, _selector(context, name)), None,
    )
    return 0


@c_abi_export("pcc_metal_device_objc_send_void1")
def _send_void1(context, target, name, arg0) -> i64:
    call_c_abi(
        load_ptr(context, CONTEXT_SEND), "void", ("ptr", "ptr", "ptr"),
        (target, _selector(context, name), arg0), None,
    )
    return 0


@c_abi_export("pcc_metal_device_objc_send_void_word")
def _send_void_word(context, target, name, value: i64) -> i64:
    call_c_abi(
        load_ptr(context, CONTEXT_SEND), "void", ("ptr", "ptr", "i64"),
        (target, _selector(context, name), value), None,
    )
    return 0


@c_abi_export("pcc_metal_device_objc_send_bind")
def _send_bind(context, encoder, name, pointer, extent: i64, slot: i64) -> i64:
    """``setBuffer:offset:atIndex:`` and ``setBytes:length:atIndex:`` shape."""
    call_c_abi(
        load_ptr(context, CONTEXT_SEND), "void",
        ("ptr", "ptr", "ptr", "i64", "i64"),
        (encoder, _selector(context, name), pointer, extent, slot), None,
    )
    return 0


@c_abi_export("pcc_metal_device_objc_pool_push")
def _pool_push(context):
    out = stack_alloc(8)
    call_c_abi(load_ptr(context, CONTEXT_POOL_PUSH), "ptr", (), (), out)
    return load_ptr(out, 0)


@c_abi_export("pcc_metal_device_objc_pool_pop")
def _pool_pop(context, pool) -> i64:
    call_c_abi(load_ptr(context, CONTEXT_POOL_POP), "void", ("ptr",), (pool,), None)
    return 0


@c_abi_export("pcc_metal_device_objc_string")
def _string(context, text):
    """An autoreleased NSString for NUL-terminated UTF-8 ``text``."""
    return _send1(
        context, load_ptr(context, CONTEXT_STRING_CLASS),
        cstr("stringWithUTF8String:"), text,
    )


@c_abi_export("pcc_metal_device_copy_text")
def _copy_text(text, out, capacity: i64) -> i64:
    """Copy a C string, truncating to ``capacity`` and always terminating it."""
    if ptr_is_null(out) or capacity <= 0:
        return 0
    count: i64 = 0
    if not ptr_is_null(text):
        done: i64 = 0
        while done == 0 and count < capacity - 1:
            byte: i64 = load_i8(text, count)
            if byte == 0:
                done = 1
            else:
                store_i8(out, count, byte)
                count = count + 1
    store_i8(out, count, 0)
    return count


@c_abi_export("pcc_metal_device_describe_error")
def _describe_error(context, error, out, capacity: i64) -> i64:
    if ptr_is_null(error):
        return _copy_text(null(), out, capacity)
    description = _send(context, error, cstr("localizedDescription"))
    if ptr_is_null(description):
        return _copy_text(null(), out, capacity)
    return _copy_text(
        _send(context, description, cstr("UTF8String")), out, capacity
    )


@c_abi_export("pcc_metal_device_dispose")
def _dispose(context) -> i64:
    queue = load_ptr(context, CONTEXT_QUEUE)
    if not ptr_is_null(queue):
        _send_void(context, queue, cstr("release"))
    device = load_ptr(context, CONTEXT_DEVICE)
    if not ptr_is_null(device):
        _send_void(context, device, cstr("release"))
    dynamic_library_close(load_ptr(context, CONTEXT_METAL))
    dynamic_library_close(load_ptr(context, CONTEXT_OBJC))
    free(context)
    return STATUS_OK


@c_abi_export("pcc_metal_device_open")
def pcc_metal_device_open(context_out) -> i64:
    if ptr_is_null(context_out):
        return STATUS_BAD_ARGUMENT
    store_ptr(context_out, 0, null())
    objc = dynamic_library_open(cstr("/usr/lib/libobjc.A.dylib"))
    if ptr_is_null(objc):
        return STATUS_OBJC_RUNTIME_UNAVAILABLE
    metal = dynamic_library_open(
        cstr("/System/Library/Frameworks/Metal.framework/Metal")
    )
    if ptr_is_null(metal):
        dynamic_library_close(objc)
        return STATUS_METAL_FRAMEWORK_UNAVAILABLE
    send = dynamic_library_symbol(objc, cstr("objc_msgSend"))
    register = dynamic_library_symbol(objc, cstr("sel_registerName"))
    get_class = dynamic_library_symbol(objc, cstr("objc_getClass"))
    pool_push = dynamic_library_symbol(objc, cstr("objc_autoreleasePoolPush"))
    pool_pop = dynamic_library_symbol(objc, cstr("objc_autoreleasePoolPop"))
    create_device = dynamic_library_symbol(
        metal, cstr("MTLCreateSystemDefaultDevice")
    )
    if (
        ptr_is_null(send)
        or ptr_is_null(register)
        or ptr_is_null(get_class)
        or ptr_is_null(pool_push)
        or ptr_is_null(pool_pop)
        or ptr_is_null(create_device)
    ):
        dynamic_library_close(metal)
        dynamic_library_close(objc)
        return STATUS_SYMBOL_UNAVAILABLE
    context = calloc(1, CONTEXT_BYTES)
    if ptr_is_null(context):
        dynamic_library_close(metal)
        dynamic_library_close(objc)
        return STATUS_OUT_OF_MEMORY
    store_ptr(context, CONTEXT_OBJC, objc)
    store_ptr(context, CONTEXT_METAL, metal)
    store_ptr(context, CONTEXT_SEND, send)
    store_ptr(context, CONTEXT_REGISTER, register)
    store_ptr(context, CONTEXT_POOL_PUSH, pool_push)
    store_ptr(context, CONTEXT_POOL_POP, pool_pop)

    slot = stack_alloc(8)
    call_c_abi(get_class, "ptr", ("ptr",), (cstr("NSString"),), slot)
    if ptr_is_null(load_ptr(slot, 0)):
        _dispose(context)
        return STATUS_SYMBOL_UNAVAILABLE
    store_ptr(context, CONTEXT_STRING_CLASS, load_ptr(slot, 0))
    call_c_abi(get_class, "ptr", ("ptr",), (cstr("MTLCompileOptions"),), slot)
    if ptr_is_null(load_ptr(slot, 0)):
        _dispose(context)
        return STATUS_SYMBOL_UNAVAILABLE
    store_ptr(context, CONTEXT_OPTIONS_CLASS, load_ptr(slot, 0))
    store_i64(context, CONTEXT_LANGUAGE_VERSION, DEFAULT_LANGUAGE_VERSION)

    call_c_abi(create_device, "ptr", (), (), slot)
    if ptr_is_null(load_ptr(slot, 0)):
        _dispose(context)
        return STATUS_DEVICE_UNAVAILABLE
    store_ptr(context, CONTEXT_DEVICE, load_ptr(slot, 0))

    queue = _send(
        context, load_ptr(context, CONTEXT_DEVICE), cstr("newCommandQueue")
    )
    if ptr_is_null(queue):
        _dispose(context)
        return STATUS_QUEUE_UNAVAILABLE
    store_ptr(context, CONTEXT_QUEUE, queue)
    store_ptr(context_out, 0, context)
    return STATUS_OK


@c_abi_export("pcc_metal_device_close")
def pcc_metal_device_close(context) -> i64:
    if ptr_is_null(context):
        return STATUS_BAD_ARGUMENT
    return _dispose(context)


@c_abi_export("pcc_metal_device_set_language_version")
def pcc_metal_device_set_language_version(context, version: i64) -> i64:
    """Select the Metal language version for later pipeline_create calls."""
    if ptr_is_null(context) or version <= 0:
        return STATUS_BAD_ARGUMENT
    store_i64(context, CONTEXT_LANGUAGE_VERSION, version)
    return STATUS_OK


@c_abi_export("pcc_metal_device_name")
def pcc_metal_device_name(context, out, capacity: i64) -> i64:
    if ptr_is_null(context):
        return _copy_text(null(), out, capacity)
    pool = _pool_push(context)
    name = _send(context, load_ptr(context, CONTEXT_DEVICE), cstr("name"))
    count: i64 = 0
    if ptr_is_null(name):
        count = _copy_text(null(), out, capacity)
    else:
        count = _copy_text(
            _send(context, name, cstr("UTF8String")), out, capacity
        )
    _pool_pop(context, pool)
    return count


@c_abi_export("pcc_metal_device_buffer_create")
def pcc_metal_device_buffer_create(context, nbytes: i64, buffer_out) -> i64:
    if ptr_is_null(context) or ptr_is_null(buffer_out):
        return STATUS_BAD_ARGUMENT
    store_ptr(buffer_out, 0, null())
    if nbytes <= 0:
        return STATUS_BAD_ARGUMENT
    slot = stack_alloc(8)
    call_c_abi(
        load_ptr(context, CONTEXT_SEND), "ptr", ("ptr", "ptr", "i64", "i64"),
        (
            load_ptr(context, CONTEXT_DEVICE),
            _selector(context, cstr("newBufferWithLength:options:")),
            nbytes,
            STORAGE_MODE_SHARED,
        ),
        slot,
    )
    if ptr_is_null(load_ptr(slot, 0)):
        return STATUS_BUFFER_UNAVAILABLE
    store_ptr(buffer_out, 0, load_ptr(slot, 0))
    return STATUS_OK


@c_abi_export("pcc_metal_device_buffer_contents")
def pcc_metal_device_buffer_contents(context, buffer):
    if ptr_is_null(context) or ptr_is_null(buffer):
        return null()
    return _send(context, buffer, cstr("contents"))


@c_abi_export("pcc_metal_device_buffer_length")
def pcc_metal_device_buffer_length(context, buffer) -> i64:
    if ptr_is_null(context) or ptr_is_null(buffer):
        return 0
    return _send_word(context, buffer, cstr("length"))


@c_abi_export("pcc_metal_device_buffer_release")
def pcc_metal_device_buffer_release(context, buffer) -> i64:
    if ptr_is_null(context) or ptr_is_null(buffer):
        return STATUS_BAD_ARGUMENT
    _send_void(context, buffer, cstr("release"))
    return STATUS_OK


@c_abi_export("pcc_metal_device_pipeline_create")
def pcc_metal_device_pipeline_create(
    context,
    source,
    function_name,
    pipeline_out,
    diagnostic,
    diagnostic_capacity: i64,
) -> i64:
    _copy_text(null(), diagnostic, diagnostic_capacity)
    if ptr_is_null(pipeline_out):
        return STATUS_BAD_ARGUMENT
    store_ptr(pipeline_out, 0, null())
    if ptr_is_null(context) or ptr_is_null(source) or ptr_is_null(function_name):
        return STATUS_BAD_ARGUMENT
    device = load_ptr(context, CONTEXT_DEVICE)
    pool = _pool_push(context)
    options = _send(
        context,
        _send(context, load_ptr(context, CONTEXT_OPTIONS_CLASS), cstr("alloc")),
        cstr("init"),
    )
    if ptr_is_null(options):
        _pool_pop(context, pool)
        return STATUS_OUT_OF_MEMORY
    _send_void_word(
        context, options, cstr("setLanguageVersion:"),
        load_i64(context, CONTEXT_LANGUAGE_VERSION),
    )
    error_slot = stack_alloc(8)
    store_ptr(error_slot, 0, null())
    library = _send3(
        context, device, cstr("newLibraryWithSource:options:error:"),
        _string(context, source), options, error_slot,
    )
    _send_void(context, options, cstr("release"))
    if ptr_is_null(library):
        _describe_error(
            context, load_ptr(error_slot, 0), diagnostic, diagnostic_capacity
        )
        _pool_pop(context, pool)
        return STATUS_SOURCE_REJECTED
    function = _send1(
        context, library, cstr("newFunctionWithName:"),
        _string(context, function_name),
    )
    if ptr_is_null(function):
        _send_void(context, library, cstr("release"))
        _pool_pop(context, pool)
        return STATUS_FUNCTION_NOT_FOUND
    store_ptr(error_slot, 0, null())
    pipeline = _send2(
        context, device, cstr("newComputePipelineStateWithFunction:error:"),
        function, error_slot,
    )
    status: i64 = STATUS_OK
    if ptr_is_null(pipeline):
        _describe_error(
            context, load_ptr(error_slot, 0), diagnostic, diagnostic_capacity
        )
        status = STATUS_PIPELINE_REJECTED
    else:
        store_ptr(pipeline_out, 0, pipeline)
    _send_void(context, function, cstr("release"))
    _send_void(context, library, cstr("release"))
    _pool_pop(context, pool)
    return status


@c_abi_export("pcc_metal_device_pipeline_limits")
def pcc_metal_device_pipeline_limits(context, pipeline, limits_out) -> i64:
    if ptr_is_null(context) or ptr_is_null(pipeline) or ptr_is_null(limits_out):
        return STATUS_BAD_ARGUMENT
    store_i64(
        limits_out, LIMIT_MAX_THREADS,
        _send_word(context, pipeline, cstr("maxTotalThreadsPerThreadgroup")),
    )
    store_i64(
        limits_out, LIMIT_EXECUTION_WIDTH,
        _send_word(context, pipeline, cstr("threadExecutionWidth")),
    )
    store_i64(
        limits_out, LIMIT_STATIC_MEMORY,
        _send_word(context, pipeline, cstr("staticThreadgroupMemoryLength")),
    )
    return STATUS_OK


@c_abi_export("pcc_metal_device_pipeline_release")
def pcc_metal_device_pipeline_release(context, pipeline) -> i64:
    if ptr_is_null(context) or ptr_is_null(pipeline):
        return STATUS_BAD_ARGUMENT
    _send_void(context, pipeline, cstr("release"))
    return STATUS_OK


@c_abi_export("pcc_metal_device_check_bindings")
def _check_bindings(context, bindings, binding_count: i64) -> i64:
    """Validate every binding record before anything is encoded."""
    if binding_count < 0:
        return STATUS_BAD_ARGUMENT
    if binding_count > 0 and ptr_is_null(bindings):
        return STATUS_BAD_ARGUMENT
    seen: i64 = 0
    index: i64 = 0
    while index < binding_count:
        record = ptr_add(bindings, index * BINDING_BYTES)
        kind: i64 = load_i64(record, BINDING_KIND)
        slot: i64 = load_i64(record, BINDING_INDEX)
        pointer = load_ptr(record, BINDING_POINTER)
        extent: i64 = load_i64(record, BINDING_EXTENT)
        if slot < 0 or slot > MAX_BINDING_INDEX:
            return STATUS_BAD_BINDING
        if ptr_is_null(pointer):
            return STATUS_BAD_BINDING
        bit: i64 = logical_shift_left_i64(1, slot)
        if seen & bit != 0:
            return STATUS_BAD_BINDING
        seen = seen | bit
        if kind == BINDING_KIND_BUFFER:
            if extent < 0:
                return STATUS_BAD_BINDING
            if extent >= _send_word(context, pointer, cstr("length")):
                return STATUS_BAD_BINDING
        elif kind == BINDING_KIND_BYTES:
            if extent <= 0:
                return STATUS_BAD_BINDING
        else:
            return STATUS_BAD_BINDING
        index = index + 1
    return STATUS_OK


@c_abi_export("pcc_metal_device_dispatch_sync")
def pcc_metal_device_dispatch_sync(
    context,
    pipeline,
    bindings,
    binding_count: i64,
    grid,
    threads,
) -> i64:
    if ptr_is_null(context) or ptr_is_null(pipeline):
        return STATUS_BAD_ARGUMENT
    if ptr_is_null(grid) or ptr_is_null(threads):
        return STATUS_BAD_ARGUMENT
    grid_x: i64 = load_i64(grid, EXTENT_X)
    grid_y: i64 = load_i64(grid, EXTENT_Y)
    grid_z: i64 = load_i64(grid, EXTENT_Z)
    threads_x: i64 = load_i64(threads, EXTENT_X)
    threads_y: i64 = load_i64(threads, EXTENT_Y)
    threads_z: i64 = load_i64(threads, EXTENT_Z)
    if grid_x < 1 or grid_y < 1 or grid_z < 1:
        return STATUS_BAD_ARGUMENT
    if threads_x < 1 or threads_y < 1 or threads_z < 1:
        return STATUS_BAD_ARGUMENT

    # Each factor is bounded by the limit before it is multiplied, so the
    # products below cannot overflow.
    max_threads: i64 = _send_word(
        context, pipeline, cstr("maxTotalThreadsPerThreadgroup")
    )
    if threads_x > max_threads or threads_y > max_threads:
        return STATUS_DISPATCH_EXCEEDS_PIPELINE
    if threads_z > max_threads or threads_x * threads_y > max_threads:
        return STATUS_DISPATCH_EXCEEDS_PIPELINE
    if threads_x * threads_y * threads_z > max_threads:
        return STATUS_DISPATCH_EXCEEDS_PIPELINE

    checked: i64 = _check_bindings(context, bindings, binding_count)
    if checked != STATUS_OK:
        return checked

    pool = _pool_push(context)
    command_buffer = _send(
        context, load_ptr(context, CONTEXT_QUEUE), cstr("commandBuffer")
    )
    if ptr_is_null(command_buffer):
        _pool_pop(context, pool)
        return STATUS_ENCODER_UNAVAILABLE
    encoder = _send(context, command_buffer, cstr("computeCommandEncoder"))
    if ptr_is_null(encoder):
        _pool_pop(context, pool)
        return STATUS_ENCODER_UNAVAILABLE
    _send_void1(context, encoder, cstr("setComputePipelineState:"), pipeline)
    index: i64 = 0
    while index < binding_count:
        record = ptr_add(bindings, index * BINDING_BYTES)
        if load_i64(record, BINDING_KIND) == BINDING_KIND_BUFFER:
            _send_bind(
                context, encoder, cstr("setBuffer:offset:atIndex:"),
                load_ptr(record, BINDING_POINTER),
                load_i64(record, BINDING_EXTENT),
                load_i64(record, BINDING_INDEX),
            )
        else:
            _send_bind(
                context, encoder, cstr("setBytes:length:atIndex:"),
                load_ptr(record, BINDING_POINTER),
                load_i64(record, BINDING_EXTENT),
                load_i64(record, BINDING_INDEX),
            )
        index = index + 1
    call_c_abi(
        load_ptr(context, CONTEXT_SEND), "void",
        ("ptr", "ptr", "{i64,i64,i64}", "{i64,i64,i64}"),
        (
            encoder,
            _selector(
                context, cstr("dispatchThreadgroups:threadsPerThreadgroup:")
            ),
            (grid_x, grid_y, grid_z),
            (threads_x, threads_y, threads_z),
        ),
        None,
    )
    _send_void(context, encoder, cstr("endEncoding"))
    _send_void(context, command_buffer, cstr("commit"))
    _send_void(context, command_buffer, cstr("waitUntilCompleted"))
    state: i64 = _send_word(context, command_buffer, cstr("status"))
    _pool_pop(context, pool)
    if state != COMMAND_BUFFER_COMPLETED:
        return STATUS_COMMAND_FAILED
    return STATUS_OK


@c_abi_export("pcc_metal_device_align8")
def _align8(size: i64) -> i64:
    return size + 7 - (size + 7) % 8


@c_abi_export("pcc_metal_device_packet_result_bytes")
def pcc_metal_device_packet_result_bytes(packet, packet_bytes: i64) -> i64:
    """Result size for a well-formed packet, or 0 when it is malformed.

    Every length is bounded by ``packet_bytes`` before it is added, and the
    regions must end exactly at the end of the packet.
    """
    if ptr_is_null(packet) or packet_bytes < PACKET_HEADER_BYTES:
        return 0
    if packet_bytes > PACKET_MAX_BYTES:
        return 0
    if load_i64(packet, PACKET_MAGIC_AT) != PACKET_MAGIC:
        return 0
    source_bytes: i64 = load_i64(packet, PACKET_SOURCE_BYTES)
    function_bytes: i64 = load_i64(packet, PACKET_FUNCTION_BYTES)
    count: i64 = load_i64(packet, PACKET_BINDING_COUNT)
    if source_bytes < 2 or source_bytes > packet_bytes:
        return 0
    if function_bytes < 2 or function_bytes > packet_bytes:
        return 0
    if count < 0 or count > MAX_BINDING_INDEX + 1:
        return 0
    if load_i64(packet, PACKET_LANGUAGE_VERSION) < 0:
        return 0
    source_at: i64 = PACKET_HEADER_BYTES + count * PACKET_BINDING_BYTES
    function_at: i64 = source_at + _align8(source_bytes)
    offset: i64 = function_at + _align8(function_bytes)
    if offset > packet_bytes:
        return 0
    if load_i8(packet, source_at + source_bytes - 1) != 0:
        return 0
    if load_i8(packet, function_at + function_bytes - 1) != 0:
        return 0
    result: i64 = RESULT_FIXED_BYTES
    index: i64 = 0
    while index < count:
        record = ptr_add(
            packet, PACKET_HEADER_BYTES + index * PACKET_BINDING_BYTES
        )
        kind: i64 = load_i64(record, PACKET_BINDING_KIND)
        nbytes: i64 = load_i64(record, PACKET_BINDING_NBYTES)
        readback: i64 = load_i64(record, PACKET_BINDING_READBACK)
        if kind != BINDING_KIND_BUFFER and kind != BINDING_KIND_BYTES:
            return 0
        if nbytes <= 0 or nbytes > packet_bytes:
            return 0
        if readback != 0 and readback != 1:
            return 0
        if readback == 1 and kind != BINDING_KIND_BUFFER:
            return 0
        offset = offset + _align8(nbytes)
        if offset > packet_bytes:
            return 0
        if readback == 1:
            result = result + RESULT_RECORD_HEADER_BYTES + _align8(nbytes)
        index = index + 1
    if offset != packet_bytes:
        return 0
    return result


@c_abi_export("pcc_metal_device_packet_fail")
def _packet_fail(result, stage: i64, status: i64) -> i64:
    store_i64(result, RESULT_STAGE, stage)
    store_i64(result, RESULT_STATUS, status)
    return RESULT_FIXED_BYTES


@c_abi_export("pcc_metal_device_packet_release_buffers")
def _packet_release_buffers(context, bindings, binding_count: i64) -> i64:
    index: i64 = 0
    while index < binding_count:
        record = ptr_add(bindings, index * BINDING_BYTES)
        if load_i64(record, BINDING_KIND) == BINDING_KIND_BUFFER:
            if not ptr_is_null(load_ptr(record, BINDING_POINTER)):
                pcc_metal_device_buffer_release(
                    context, load_ptr(record, BINDING_POINTER)
                )
        index = index + 1
    return STATUS_OK


@c_abi_export("pcc_metal_device_launch_packet")
def pcc_metal_device_launch_packet(
    packet,
    packet_bytes: i64,
    result,
    result_capacity: i64,
) -> i64:
    """Run one launch packet and return the number of result bytes written.

    Zero means that no result could be written at all. Otherwise the result
    names the stage and status; read-back records follow only for STAGE_DONE.
    """
    if ptr_is_null(result) or result_capacity < RESULT_FIXED_BYTES:
        return 0
    memset(result, 0, RESULT_FIXED_BYTES)
    store_i64(result, RESULT_MAGIC_AT, RESULT_MAGIC)
    needed: i64 = pcc_metal_device_packet_result_bytes(packet, packet_bytes)
    if needed == 0 or result_capacity < needed:
        return _packet_fail(result, STAGE_PACKET, STATUS_BAD_ARGUMENT)
    count: i64 = load_i64(packet, PACKET_BINDING_COUNT)
    source_at: i64 = PACKET_HEADER_BYTES + count * PACKET_BINDING_BYTES
    function_at: i64 = source_at + _align8(load_i64(packet, PACKET_SOURCE_BYTES))
    data_at: i64 = function_at + _align8(load_i64(packet, PACKET_FUNCTION_BYTES))

    context_slot = stack_alloc(8)
    status: i64 = pcc_metal_device_open(context_slot)
    if status != STATUS_OK:
        return _packet_fail(result, STAGE_OPEN, status)
    context = load_ptr(context_slot, 0)
    if load_i64(packet, PACKET_LANGUAGE_VERSION) != 0:
        pcc_metal_device_set_language_version(
            context, load_i64(packet, PACKET_LANGUAGE_VERSION)
        )
    store_i64(
        result, RESULT_LANGUAGE_VERSION,
        load_i64(context, CONTEXT_LANGUAGE_VERSION),
    )

    pipeline_slot = stack_alloc(8)
    status = pcc_metal_device_pipeline_create(
        context, ptr_add(packet, source_at), ptr_add(packet, function_at),
        pipeline_slot, ptr_add(result, RESULT_HEADER_BYTES),
        RESULT_DIAGNOSTIC_BYTES,
    )
    if status != STATUS_OK:
        pcc_metal_device_close(context)
        return _packet_fail(result, STAGE_PIPELINE, status)
    pipeline = load_ptr(pipeline_slot, 0)
    limits = stack_alloc(24)
    pcc_metal_device_pipeline_limits(context, pipeline, limits)
    store_i64(result, RESULT_MAX_THREADS, load_i64(limits, LIMIT_MAX_THREADS))
    store_i64(
        result, RESULT_EXECUTION_WIDTH, load_i64(limits, LIMIT_EXECUTION_WIDTH)
    )

    bindings = calloc(count + 1, BINDING_BYTES)
    if ptr_is_null(bindings):
        pcc_metal_device_pipeline_release(context, pipeline)
        pcc_metal_device_close(context)
        return _packet_fail(result, STAGE_BUFFER, STATUS_OUT_OF_MEMORY)
    buffer_slot = stack_alloc(8)
    cursor: i64 = data_at
    index: i64 = 0
    while index < count and status == STATUS_OK:
        descriptor = ptr_add(
            packet, PACKET_HEADER_BYTES + index * PACKET_BINDING_BYTES
        )
        record = ptr_add(bindings, index * BINDING_BYTES)
        nbytes: i64 = load_i64(descriptor, PACKET_BINDING_NBYTES)
        store_i64(record, BINDING_INDEX, load_i64(descriptor, PACKET_BINDING_INDEX))
        if load_i64(descriptor, PACKET_BINDING_KIND) == BINDING_KIND_BUFFER:
            store_i64(record, BINDING_KIND, BINDING_KIND_BUFFER)
            status = pcc_metal_device_buffer_create(context, nbytes, buffer_slot)
            if status == STATUS_OK:
                store_ptr(record, BINDING_POINTER, load_ptr(buffer_slot, 0))
                store_i64(record, BINDING_EXTENT, 0)
                memcpy(
                    pcc_metal_device_buffer_contents(
                        context, load_ptr(buffer_slot, 0)
                    ),
                    ptr_add(packet, cursor),
                    nbytes,
                )
        else:
            store_i64(record, BINDING_KIND, BINDING_KIND_BYTES)
            store_ptr(record, BINDING_POINTER, ptr_add(packet, cursor))
            store_i64(record, BINDING_EXTENT, nbytes)
        cursor = cursor + _align8(nbytes)
        index = index + 1

    written: i64 = RESULT_FIXED_BYTES
    if status != STATUS_OK:
        _packet_fail(result, STAGE_BUFFER, status)
    else:
        status = pcc_metal_device_dispatch_sync(
            context, pipeline, bindings, count,
            ptr_add(packet, PACKET_GRID), ptr_add(packet, PACKET_THREADS),
        )
        if status != STATUS_OK:
            _packet_fail(result, STAGE_DISPATCH, status)
        else:
            records: i64 = 0
            index = 0
            while index < count:
                descriptor = ptr_add(
                    packet, PACKET_HEADER_BYTES + index * PACKET_BINDING_BYTES
                )
                if load_i64(descriptor, PACKET_BINDING_READBACK) == 1:
                    nbytes = load_i64(descriptor, PACKET_BINDING_NBYTES)
                    out = ptr_add(result, written)
                    memset(out, 0, RESULT_RECORD_HEADER_BYTES + _align8(nbytes))
                    store_i64(
                        out, RESULT_RECORD_INDEX,
                        load_i64(descriptor, PACKET_BINDING_INDEX),
                    )
                    store_i64(out, RESULT_RECORD_NBYTES, nbytes)
                    memcpy(
                        ptr_add(out, RESULT_RECORD_HEADER_BYTES),
                        pcc_metal_device_buffer_contents(
                            context,
                            load_ptr(
                                ptr_add(bindings, index * BINDING_BYTES),
                                BINDING_POINTER,
                            ),
                        ),
                        nbytes,
                    )
                    written = (
                        written + RESULT_RECORD_HEADER_BYTES + _align8(nbytes)
                    )
                    records = records + 1
                index = index + 1
            store_i64(result, RESULT_READBACK_COUNT, records)
            store_i64(result, RESULT_STAGE, STAGE_DONE)
            store_i64(result, RESULT_STATUS, STATUS_OK)

    _packet_release_buffers(context, bindings, count)
    free(bindings)
    pcc_metal_device_pipeline_release(context, pipeline)
    pcc_metal_device_close(context)
    return written


@c_abi_export("pcc_metal_device_packet_main")
def pcc_metal_device_packet_main() -> i64:
    """Process entry: read one launch packet from fd 0, write its result to fd 1."""
    capacity: i64 = 65536
    packet = calloc(1, capacity)
    if ptr_is_null(packet):
        return PROCESS_INPUT_FAILED
    size: i64 = 0
    done: i64 = 0
    while done == 0:
        if size == capacity:
            if capacity >= PACKET_MAX_BYTES:
                free(packet)
                return PROCESS_INPUT_FAILED
            capacity = capacity * 2
            grown = realloc(packet, capacity)
            if ptr_is_null(grown):
                free(packet)
                return PROCESS_INPUT_FAILED
            packet = grown
        got: i64 = read(0, ptr_add(packet, size), capacity - size)
        if got < 0:
            free(packet)
            return PROCESS_INPUT_FAILED
        if got == 0:
            done = 1
        else:
            size = size + got

    result_capacity: i64 = pcc_metal_device_packet_result_bytes(packet, size)
    if result_capacity < RESULT_FIXED_BYTES:
        result_capacity = RESULT_FIXED_BYTES
    result = calloc(1, result_capacity)
    if ptr_is_null(result):
        free(packet)
        return PROCESS_OUTPUT_FAILED
    written: i64 = pcc_metal_device_launch_packet(
        packet, size, result, result_capacity
    )
    status: i64 = PROCESS_OK
    if written == 0:
        status = PROCESS_OUTPUT_FAILED
    offset: i64 = 0
    while offset < written and status == PROCESS_OK:
        sent: i64 = write(1, ptr_add(result, offset), written - offset)
        if sent <= 0:
            status = PROCESS_OUTPUT_FAILED
        else:
            offset = offset + sent
    free(result)
    free(packet)
    return status
