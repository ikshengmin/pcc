"""One raw object-layout and GC slot-role contract."""

from pcc import i64
from pcc.extern import c_abi_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import (
    abi_constant,
    atomic_load_i64,
    call_void_ptr_i64_ptr,
    cstr,
    global_load_ptr,
    int_to_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True


pcc_capi_is_cext_type_tag = extern(
    "pcc_capi_is_cext_type_tag", (c_int64,), c_int64
)
pcc_capi_visit_cext_object_slots_i64 = extern(
    "pcc_capi_visit_cext_object_slots_i64",
    (c_ptr, c_ptr, c_ptr),
    c_int32
)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)


@c_abi_export("pcc_gc_memoryview_initialize_owned_buffer")
def pcc_gc_memoryview_initialize_owned_buffer(obj, buffer_view) -> i64:
    if ptr_is_null(buffer_view) == 0:
        store_ptr(buffer_view, 72, int_to_ptr(0))  # fresh opaque internal state
    return _memoryview_write_owned_buffer(obj, buffer_view)


@c_abi_export("pcc_gc_memoryview_write_owned_buffer")
def _memoryview_write_owned_buffer(obj, buffer_view) -> i64:
    # Rebuild a memoryview's non-owning Py_buffer aliases from base@16.
    if ptr_is_null(obj) != 0 or ptr_is_null(buffer_view) != 0:
        return 0
    if is_tagged_int(obj) != 0 or load_i32(
        obj, abi_constant("object.header.type_tag_offset")
    ) != abi_constant("object.type.memoryview"):
        return 0
    base = pcc_gc_load_ptr(
        obj, ptr_add(obj, abi_constant("object.memoryview.base_offset"))
    )
    if ptr_is_null(base) != 0 or is_tagged_int(base) != 0:
        return 0

    exporter = base
    exporter_tag: i64 = load_i32(
        exporter, abi_constant("object.header.type_tag_offset")
    )
    while exporter_tag == abi_constant("object.type.memoryview"):
        exporter = pcc_gc_load_ptr(
            exporter,
            ptr_add(exporter, abi_constant("object.memoryview.base_offset")),
        )
        if ptr_is_null(exporter) != 0 or is_tagged_int(exporter) != 0:
            return 0
        exporter_tag = load_i32(
            exporter, abi_constant("object.header.type_tag_offset")
        )
    if exporter_tag != abi_constant(
        "object.type.bytes"
    ) and exporter_tag != abi_constant("object.type.bytearray"):
        return 0

    length: i64 = load_i64(
        exporter, abi_constant("object.bytes.byte_len_offset")
    )
    # Every public field is written explicitly. In particular, refresh must
    # never clear internal@72, even temporarily: concurrent refreshes could
    # otherwise lose the cache's private exporter root/lease record.
    store_ptr(
        buffer_view,
        0,
        ptr_add(exporter, abi_constant("object.bytes.data_offset")),
    )  # buf
    store_ptr(buffer_view, 8, base)  # derived alias; base@16 owns it
    store_i64(buffer_view, 16, length)
    store_i64(buffer_view, 24, 1)  # itemsize
    if exporter_tag == abi_constant("object.type.bytes"):
        store_i32(buffer_view, 32, 1)  # readonly
    else:
        store_i32(buffer_view, 32, 0)
    store_i32(buffer_view, 36, 1)  # ndim
    store_ptr(buffer_view, 40, cstr("B"))
    store_ptr(buffer_view, 48, ptr_add(buffer_view, 80))
    store_ptr(buffer_view, 56, ptr_add(buffer_view, 88))
    store_ptr(buffer_view, 64, int_to_ptr(0))  # suboffsets
    store_i64(buffer_view, 80, length)  # inline shape
    store_i64(buffer_view, 88, 1)  # inline stride
    return 1


@c_abi_export("pcc_gc_memoryview_refresh_owned_buffer")
def pcc_gc_memoryview_refresh_owned_buffer(obj) -> i64:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return 0
    if load_i32(
        obj, abi_constant("object.header.type_tag_offset")
    ) != abi_constant("object.type.memoryview"):
        return 0
    bits: i64 = atomic_load_i64(obj, 24, "acquire")
    if bits == 0:
        return 0
    return _memoryview_write_owned_buffer(obj, int_to_ptr(bits))


@c_abi_export("pcc_gc_object_slots_visit_slot")
def _visit_slot(
    slot_base,
    slot_offset: i64,
    role: i64,
    visitor,
    context,
) -> None:
    call_void_ptr_i64_ptr(
        visitor, ptr_add(slot_base, slot_offset), role, context
    )


@c_abi_export("pcc_gc_object_slots_visit_core_container_slots")
def _visit_core_container_slots(o, visitor, context) -> i64:
    tag: i64 = load_i32(o, abi_constant("object.header.type_tag_offset"))
    if tag == abi_constant("object.type.list"):
        length: i64 = load_i64(o, abi_constant("object.list.length_offset"))
        items = load_ptr(o, abi_constant("object.list.items_offset"))
        if ptr_is_null(items) == 0:
            index: i64 = 0
            while index < length:
                _visit_slot(items, index * 8, 1, visitor, context)
                index = index + 1
        return 1
    if tag == abi_constant("object.type.tuple"):
        length = load_i64(o, abi_constant("object.tuple.length_offset"))
        index: i64 = 0
        while index < length:
            _visit_slot(
                o,
                abi_constant("object.tuple.items_offset") + index * 8,
                1,
                visitor,
                context,
            )
            index = index + 1
        return 1
    if tag == abi_constant("object.type.dict"):
        entries = load_ptr(o, abi_constant("object.dict.entries_offset"))
        if ptr_is_null(entries) == 0:
            used: i64 = load_i64(
                o, abi_constant("object.dict.entries_used_offset")
            )
            index: i64 = 0
            while index < used:
                offset: i64 = index * abi_constant("object.dict_entry.size")
                if ptr_is_null(
                    load_ptr(
                        entries,
                        offset + abi_constant("object.dict_entry.key_offset"),
                    )
                ) == 0:
                    _visit_slot(
                        entries,
                        offset + abi_constant("object.dict_entry.key_offset"),
                        1,
                        visitor,
                        context,
                    )
                    _visit_slot(
                        entries,
                        offset + abi_constant("object.dict_entry.value_offset"),
                        1,
                        visitor,
                        context,
                    )
                index = index + 1
        return 1
    if tag == abi_constant("object.type.set"):
        entries = load_ptr(o, 40)
        if ptr_is_null(entries) == 0:
            dummy = global_load_ptr("py_set_dummy")
            capacity: i64 = load_i64(o, 24)
            index: i64 = 0
            while index < capacity:
                key = load_ptr(entries, index * 16 + 8)
                if ptr_is_null(key) == 0 and ptr_eq(key, dummy) == 0:
                    _visit_slot(entries, index * 16 + 8, 1, visitor, context)
                index = index + 1
        return 1
    if tag == abi_constant("object.type.vthread_channel"):
        kind: i64 = load_i64(
            o, abi_constant("object.vthread_channel.core.kind_offset")
        )
        if kind == 0:
            capacity = load_i64(
                o, abi_constant("object.vthread_channel.core.capacity_offset")
            )
            if capacity < 0 or capacity > 1048576:
                return 1
            index: i64 = 0
            while index < capacity:
                _visit_slot(
                    o,
                    abi_constant("object.vthread_channel.core.items_offset")
                    + index * abi_constant("object.pointer.size"),
                    1,
                    visitor,
                    context,
                )
                index = index + 1
            return 1
        if kind == 1 or kind == 2:
            _visit_slot(
                o,
                abi_constant("object.vthread_channel.endpoint.core_offset"),
                1,
                visitor,
                context,
            )
        return 1
    return 0


@c_abi_export("pcc_gc_object_slots_visit_fixed_owner_slots")
def _visit_fixed_owner_slots(o, visitor, context) -> i64:
    tag: i64 = load_i32(o, abi_constant("object.header.type_tag_offset"))
    if tag == abi_constant("object.type.func"):
        _visit_slot(o, 24, 1, visitor, context)
        _visit_slot(o, 32, 1, visitor, context)
        _visit_slot(o, 40, 1, visitor, context)
        _visit_slot(o, 64, 1, visitor, context)
        _visit_slot(o, 80, 1, visitor, context)
        _visit_slot(o, 88, 1, visitor, context)
        return 1
    if tag == abi_constant("object.type.iter"):
        _visit_slot(o, 16, 1, visitor, context)
        return 1
    if tag == abi_constant("object.type.gen"):
        _visit_slot(o, 24, 1, visitor, context)
        _visit_slot(o, 48, 1, visitor, context)
        if (load_i32(o, abi_constant("object.header.flags_offset")) & abi_constant("object.gen.inline_frame_flag")) != 0:
            count: i64 = load_i64(o, abi_constant("object.gen.inline_length_offset"))
            index: i64 = 0
            while index < count:
                _visit_slot(o, abi_constant("object.gen.inline_items_offset") + index * abi_constant("object.pointer.size"), 1, visitor, context)
                index += 1
        return 1
    if tag == abi_constant("object.type.coroutine"):
        _visit_slot(o, 32, 1, visitor, context)
        _visit_slot(o, 40, 1, visitor, context)
        _visit_slot(o, 48, 1, visitor, context)
        return 1
    if tag == abi_constant("object.type.task"):
        _visit_slot(o, 16, 1, visitor, context)
        _visit_slot(o, 24, 1, visitor, context)
        _visit_slot(o, 32, 1, visitor, context)
        return 1
    if tag == abi_constant("object.type.virtual_thread"):
        _visit_slot(o, 16, 1, visitor, context)
        _visit_slot(o, 24, 1, visitor, context)
        _visit_slot(o, 72, 1, visitor, context)
        _visit_slot(o, 112, 1, visitor, context)
        _visit_slot(
            o,
            abi_constant("object.virtual_thread.channel_owner_a_offset"),
            1,
            visitor,
            context,
        )
        _visit_slot(
            o,
            abi_constant("object.virtual_thread.channel_owner_b_offset"),
            1,
            visitor,
            context,
        )
        _visit_slot(
            o,
            abi_constant("object.virtual_thread.channel_value_offset"),
            1,
            visitor,
            context,
        )
        return 1
    if tag == abi_constant("object.type.exc"):
        _visit_slot(o, 16, 1, visitor, context)
        _visit_slot(o, 24, 1, visitor, context)
        _visit_slot(o, 32, 1, visitor, context)
        _visit_slot(o, 40, 1, visitor, context)
        return 1
    if tag == abi_constant("object.type.property"):
        _visit_slot(
            o, abi_constant("object.property.fget_offset"), 1, visitor, context
        )
        _visit_slot(
            o, abi_constant("object.property.fset_offset"), 1, visitor, context
        )
        _visit_slot(
            o, abi_constant("object.property.fdel_offset"), 1, visitor, context
        )
        return 1
    if tag == abi_constant("object.type.classmethod"):
        _visit_slot(
            o,
            abi_constant("object.classmethod.func_offset"),
            1,
            visitor,
            context,
        )
        return 1
    if tag == abi_constant("object.type.staticmethod"):
        _visit_slot(
            o,
            abi_constant("object.staticmethod.func_offset"),
            1,
            visitor,
            context,
        )
        return 1
    if tag == abi_constant("object.type.memoryview"):
        _visit_slot(o, 16, 1, visitor, context)
        return 1
    if tag == abi_constant("object.type.thread"):
        _visit_slot(o, 24, 1, visitor, context)
        _visit_slot(o, 32, 1, visitor, context)
        _visit_slot(o, 40, 1, visitor, context)
        return 1
    return 0


@c_abi_export("pcc_gc_object_slots_visit_weakref_slots")
def _visit_weakref_slots(o, visitor, context) -> i64:
    if load_i32(
        o, abi_constant("object.header.type_tag_offset")
    ) != abi_constant("object.type.weakref"):
        return 0
    _visit_slot(o, 16, 3, visitor, context)
    _visit_slot(o, 24, 1, visitor, context)
    return 1


@c_abi_export("pcc_gc_object_slots_visit_continuation_slots")
def _visit_continuation_slots(o, visitor, context) -> i64:
    if load_i32(
        o, abi_constant("object.header.type_tag_offset")
    ) != abi_constant("object.type.continuation"):
        return 0
    chunk = load_ptr(o, 24)
    if ptr_is_null(chunk) == 0:
        slots = load_ptr(chunk, 16)
        if ptr_is_null(slots) == 0:
            count: i64 = load_i64(chunk, 8)
            index: i64 = 0
            while index < count:
                _visit_slot(slots, index * 8, 1, visitor, context)
                index = index + 1
    return 1


@c_abi_export("pcc_gc_object_slots_visit_class_slots")
def _visit_class_slots(o, visitor, context) -> i64:
    if load_i32(
        o, abi_constant("object.header.type_tag_offset")
    ) != abi_constant("object.type.class"):
        return 0
    count: i64 = load_i32(o, abi_constant("object.class.n_bases_offset"))
    slots = load_ptr(o, abi_constant("object.class.bases_offset"))
    index: i64 = 0
    if ptr_is_null(slots) == 0:
        while index < count:
            _visit_slot(
                slots,
                index * abi_constant("object.pointer.size"),
                2,
                visitor,
                context,
            )
            index = index + 1
    # The MRO is derived metadata, not an owning edge: it starts with the
    # class itself, and the bases keep the rest alive. Update-only (role 3),
    # so reference counting and trial deletion never count it.
    count = load_i32(o, abi_constant("object.class.n_mro_offset"))
    slots = load_ptr(o, abi_constant("object.class.mro_offset"))
    index: i64 = 0
    if ptr_is_null(slots) == 0:
        while index < count:
            _visit_slot(
                slots,
                index * abi_constant("object.pointer.size"),
                3,
                visitor,
                context,
            )
            index = index + 1
    count = load_i32(o, abi_constant("object.class.n_methods_offset"))
    slots = load_ptr(o, abi_constant("object.class.methods_offset"))
    index: i64 = 0
    if ptr_is_null(slots) == 0:
        while index < count:
            _visit_slot(
                slots,
                index * abi_constant("object.class_method.size")
                + abi_constant("object.class_method.func_offset"),
                3,
                visitor,
                context,
            )
            index = index + 1
    _visit_slot(
        o,
        abi_constant("object.class.del_method_offset"),
        3,
        visitor,
        context,
    )
    _visit_slot(
        o,
        abi_constant("object.class.attrs_offset"),
        1,
        visitor,
        context,
    )
    _visit_slot(
        o,
        abi_constant("object.class.metaclass_offset"),
        1,
        visitor,
        context,
    )
    return 1


@c_abi_export("pcc_gc_object_slots_visit_instance_slots")
def _visit_instance_slots(o, visitor, context) -> i64:
    tag: i64 = load_i32(o, abi_constant("object.header.type_tag_offset"))
    if pcc_capi_is_cext_type_tag(tag) != 0:
        return 0
    if (
        tag != abi_constant("object.type.instance")
        and tag != abi_constant("object.type.valuebox")
        and tag < abi_constant("object.type.user_class_start")
    ):
        return 0
    cls = load_ptr(o, abi_constant("object.instance.cls_offset"))
    if ptr_is_null(cls) != 0:
        return 1
    _visit_slot(
        o,
        abi_constant("object.instance.cls_offset"),
        2,
        visitor,
        context,
    )
    cls = load_ptr(o, abi_constant("object.instance.cls_offset"))
    if ptr_is_null(cls) != 0:
        return 1
    # Class instance_size describes every physical owning slot, including
    # the unchanged dynamic owner and appended immutable builtin payloads.
    count: i64 = (load_i32(cls, abi_constant("object.class.instance_size_offset")) - abi_constant("object.instance.fields_offset")) // abi_constant("object.pointer.size")
    minimum: i64 = load_i32(cls, abi_constant("object.class.n_fields_offset")) + 1
    if minimum < 1:
        minimum = 1
    if count < minimum:
        count = minimum
    index: i64 = 0
    while index < count:
        _visit_slot(o, abi_constant("object.instance.fields_offset") + index * abi_constant("object.pointer.size"), 1, visitor, context)
        index = index + 1

    return 1


@c_abi_export("pcc_gc_object_slots_has_no_pointer_slots")
def _has_no_pointer_slots(o) -> i64:
    tag: i64 = load_i32(o, abi_constant("object.header.type_tag_offset"))
    if (
        tag == abi_constant("object.type.none")
        or tag == abi_constant("object.type.bool")
        or tag == abi_constant("object.type.int")
        or tag == abi_constant("object.type.float")
        or tag == abi_constant("object.type.str")
    ):
        return 1
    if (
        tag == abi_constant("object.type.complex")
        or tag == abi_constant("object.type.bytes")
        or tag == abi_constant("object.type.bytearray")
        or tag == abi_constant("object.type.file")
        or tag == abi_constant("object.type.cpy_handle")
    ):
        return 1
    if (
        tag == abi_constant("object.type.thread_lock")
        or tag == abi_constant("object.type.thread_rlock")
        or tag == abi_constant("object.type.thread_event")
        or tag == abi_constant("object.type.thread_condition")
        or tag == abi_constant("object.type.thread_semaphore")
    ):
        return 1
    return 0


@c_abi_export("pcc_gc_visit_object_slots_slice")
def pcc_gc_visit_object_slots_slice(
    o,
    cursor: i64,
    limit: i64,
    visitor,
    context,
    state_out,
) -> i64:
    """Visit a bounded current-layout slice without retaining raw slots."""
    if ptr_is_null(state_out) != 0:
        return 0
    store_i64(state_out, 0, -1)
    store_i64(state_out, 8, 0)
    if (
        ptr_is_null(o) != 0
        or is_tagged_int(o) != 0
        or ptr_is_null(visitor) != 0
        or cursor < 0
        or limit <= 0
    ):
        return 0
    tag: i64 = load_i32(o, abi_constant("object.header.type_tag_offset"))
    if tag < abi_constant("object.type.none"):
        return 0
    if _has_no_pointer_slots(o) != 0:
        return 1
    if pcc_capi_is_cext_type_tag(tag) != 0:
        return 0

    total: i64 = 0
    family: i64 = 0
    if tag == abi_constant("object.type.list"):
        total = load_i64(o, abi_constant("object.list.length_offset"))
        family = 1
    elif tag == abi_constant("object.type.tuple"):
        total = load_i64(o, abi_constant("object.tuple.length_offset"))
        family = 2
    elif tag == abi_constant("object.type.dict"):
        total = load_i64(
            o, abi_constant("object.dict.entries_used_offset")
        ) * 2
        family = 3
    elif tag == abi_constant("object.type.set"):
        total = load_i64(o, 24)
        family = 4
    elif tag == abi_constant("object.type.vthread_channel"):
        kind: i64 = load_i64(
            o, abi_constant("object.vthread_channel.core.kind_offset")
        )
        if kind == 0:
            total = load_i64(
                o, abi_constant("object.vthread_channel.core.capacity_offset")
            )
            if total > 1048576:
                total = 0
            family = 5
        elif kind == 1 or kind == 2:
            total = 1
            family = 6
        else:
            return 1
    elif tag == abi_constant("object.type.func"):
        total = 6
        family = 7
    elif tag == abi_constant("object.type.iter"):
        total = 1
        family = 8
    elif tag == abi_constant("object.type.gen"):
        total = 2
        if (load_i32(o, abi_constant("object.header.flags_offset")) & abi_constant("object.gen.inline_frame_flag")) != 0:
            total = total + load_i64(o, abi_constant("object.gen.inline_length_offset"))
        family = 9
    elif tag == abi_constant("object.type.coroutine"):
        total = 3
        family = 10
    elif tag == abi_constant("object.type.task"):
        total = 3
        family = 11
    elif tag == abi_constant("object.type.virtual_thread"):
        total = 7
        family = 12
    elif tag == abi_constant("object.type.exc"):
        total = 4
        family = 13
    elif tag == abi_constant("object.type.property"):
        total = 3
        family = 14
    elif tag == abi_constant("object.type.classmethod"):
        total = 1
        family = 15
    elif tag == abi_constant("object.type.staticmethod"):
        total = 1
        family = 16
    elif tag == abi_constant("object.type.memoryview"):
        total = 1
        family = 17
    elif tag == abi_constant("object.type.thread"):
        total = 3
        family = 18
    elif tag == abi_constant("object.type.weakref"):
        total = 2
        family = 19
    elif tag == abi_constant("object.type.continuation"):
        chunk = load_ptr(o, 24)
        if ptr_is_null(chunk) == 0 and ptr_is_null(load_ptr(chunk, 16)) == 0:
            total = load_i64(chunk, 8)
        family = 20
    elif tag == abi_constant("object.type.class"):
        n_bases: i64 = load_i32(o, abi_constant("object.class.n_bases_offset"))
        n_mro: i64 = load_i32(o, abi_constant("object.class.n_mro_offset"))
        n_methods: i64 = load_i32(
            o, abi_constant("object.class.n_methods_offset")
        )
        if n_bases < 0:
            n_bases = 0
        if n_mro < 0:
            n_mro = 0
        if n_methods < 0:
            n_methods = 0
        total = n_bases + n_mro + n_methods + 3
        family = 21
    elif (
        tag == abi_constant("object.type.instance")
        or tag == abi_constant("object.type.valuebox")
        or tag >= abi_constant("object.type.user_class_start")
    ):
        cls = load_ptr(o, abi_constant("object.instance.cls_offset"))
        if ptr_is_null(cls) != 0:
            return 1
        if load_i32(
            cls, abi_constant("object.header.type_tag_offset")
        ) != abi_constant("object.type.class"):
            return 1
        n_fields: i64 = load_i32(
            cls, abi_constant("object.class.n_fields_offset")
        )
        if n_fields < 0:
            n_fields = 0
        count: i64 = (load_i32(cls, abi_constant("object.class.instance_size_offset")) - abi_constant("object.instance.fields_offset")) // abi_constant("object.pointer.size")
        if count < n_fields + 1:
            count = n_fields + 1
        total = 1 + count
        family = 22
    else:
        return 0
    if total < 0:
        total = 0

    examined: i64 = 0
    while cursor < total and examined < limit:
        slot_base = o
        slot_offset: i64 = 0
        role: i64 = 1
        present: i64 = 1
        if family == 1:
            slot_base = load_ptr(o, abi_constant("object.list.items_offset"))
            slot_offset = cursor * 8
            if ptr_is_null(slot_base) != 0:
                present = 0
        elif family == 2:
            slot_offset = abi_constant("object.tuple.items_offset") + cursor * 8
        elif family == 3:
            slot_base = load_ptr(o, abi_constant("object.dict.entries_offset"))
            entry_index: i64 = cursor // 2
            entry_offset: i64 = entry_index * abi_constant("object.dict_entry.size")
            if ptr_is_null(slot_base) != 0 or ptr_is_null(
                load_ptr(
                    slot_base,
                    entry_offset + abi_constant("object.dict_entry.key_offset"),
                )
            ) != 0:
                present = 0
            elif cursor % 2 == 0:
                slot_offset = entry_offset + abi_constant(
                    "object.dict_entry.key_offset"
                )
            else:
                slot_offset = entry_offset + abi_constant(
                    "object.dict_entry.value_offset"
                )
        elif family == 4:
            slot_base = load_ptr(o, 40)
            slot_offset = cursor * 16 + 8
            if ptr_is_null(slot_base) != 0:
                present = 0
            else:
                key = load_ptr(slot_base, slot_offset)
                if ptr_is_null(key) != 0 or ptr_eq(
                    key, global_load_ptr("py_set_dummy")
                ) != 0:
                    present = 0
        elif family == 5:
            slot_offset = (
                abi_constant("object.vthread_channel.core.items_offset")
                + cursor * abi_constant("object.pointer.size")
            )
        elif family == 6:
            slot_offset = abi_constant(
                "object.vthread_channel.endpoint.core_offset"
            )
        elif family == 7:
            if cursor == 0:
                slot_offset = 24
            elif cursor == 1:
                slot_offset = 32
            elif cursor == 2:
                slot_offset = 40
            elif cursor == 3:
                slot_offset = 64
            elif cursor == 4:
                slot_offset = 80
            else:
                slot_offset = 88
        elif family == 8:
            slot_offset = 16
        elif family == 9:
            if cursor == 0:
                slot_offset = 24
            elif cursor == 1:
                slot_offset = 48
            else:
                slot_offset = abi_constant("object.gen.inline_items_offset") + (cursor - 2) * abi_constant("object.pointer.size")
        elif family == 10:
            slot_offset = 32 + cursor * 8
        elif family == 11:
            slot_offset = 16 + cursor * 8
        elif family == 12:
            if cursor == 0:
                slot_offset = 16
            elif cursor == 1:
                slot_offset = 24
            elif cursor == 2:
                slot_offset = 72
            elif cursor == 3:
                slot_offset = 112
            elif cursor == 4:
                slot_offset = abi_constant(
                    "object.virtual_thread.channel_owner_a_offset"
                )
            elif cursor == 5:
                slot_offset = abi_constant(
                    "object.virtual_thread.channel_owner_b_offset"
                )
            else:
                slot_offset = abi_constant(
                    "object.virtual_thread.channel_value_offset"
                )
        elif family == 13:
            slot_offset = 16 + cursor * 8
        elif family == 14:
            if cursor == 0:
                slot_offset = abi_constant("object.property.fget_offset")
            elif cursor == 1:
                slot_offset = abi_constant("object.property.fset_offset")
            else:
                slot_offset = abi_constant("object.property.fdel_offset")
        elif family == 15:
            slot_offset = abi_constant("object.classmethod.func_offset")
        elif family == 16:
            slot_offset = abi_constant("object.staticmethod.func_offset")
        elif family == 17:
            slot_offset = 16
        elif family == 18:
            slot_offset = 24 + cursor * 8
        elif family == 19:
            slot_offset = 16 + cursor * 8
            if cursor == 0:
                role = 3
        elif family == 20:
            chunk = load_ptr(o, 24)
            if ptr_is_null(chunk) != 0:
                present = 0
            else:
                slot_base = load_ptr(chunk, 16)
                slot_offset = cursor * 8
                if ptr_is_null(slot_base) != 0:
                    present = 0
        elif family == 21:
            n_bases = load_i32(o, abi_constant("object.class.n_bases_offset"))
            n_mro = load_i32(o, abi_constant("object.class.n_mro_offset"))
            n_methods = load_i32(
                o, abi_constant("object.class.n_methods_offset")
            )
            if n_bases < 0:
                n_bases = 0
            if n_mro < 0:
                n_mro = 0
            if n_methods < 0:
                n_methods = 0
            if cursor < n_bases:
                slot_base = load_ptr(o, abi_constant("object.class.bases_offset"))
                slot_offset = cursor * 8
                role = 2
            elif cursor < n_bases + n_mro:
                # Derived metadata, as in _visit_class_slots.
                slot_base = load_ptr(o, abi_constant("object.class.mro_offset"))
                slot_offset = (cursor - n_bases) * 8
                role = 3
            elif cursor < n_bases + n_mro + n_methods:
                slot_base = load_ptr(o, abi_constant("object.class.methods_offset"))
                slot_offset = (
                    (cursor - n_bases - n_mro)
                    * abi_constant("object.class_method.size")
                    + abi_constant("object.class_method.func_offset")
                )
                role = 3
            elif cursor == n_bases + n_mro + n_methods:
                slot_offset = abi_constant("object.class.del_method_offset")
                role = 3
            elif cursor == n_bases + n_mro + n_methods + 1:
                slot_offset = abi_constant("object.class.attrs_offset")
            else:
                slot_offset = abi_constant("object.class.metaclass_offset")
                role = 1
            if ptr_is_null(slot_base) != 0:
                present = 0
        else:
            if cursor == 0:
                slot_offset = abi_constant("object.instance.cls_offset")
                role = 2
            else:
                slot_offset = (
                    abi_constant("object.instance.fields_offset")
                    + (cursor - 1) * abi_constant("object.pointer.size")
                )
        if present != 0:
            _visit_slot(slot_base, slot_offset, role, visitor, context)
        cursor = cursor + 1
        examined = examined + 1
    if cursor < total:
        store_i64(state_out, 0, cursor)
    store_i64(state_out, 8, examined)
    return 1


@c_abi_export("pcc_gc_visit_object_slots")
def pcc_gc_visit_object_slots(o, visitor, context) -> i64:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return 0
    state = stack_alloc(16)
    handled: i64 = pcc_gc_visit_object_slots_slice(
        o, 0, 9223372036854775807, visitor, context, state
    )
    if handled != 0:
        return handled
    cext_handled: i64 = pcc_capi_visit_cext_object_slots_i64(
        o, visitor, context
    )
    if cext_handled != 0:
        return cext_handled
    return 0

# Diagnostic full-heap reference, independent of any experiment state/TLS.
# Old=every registry-owned object OUTSIDE the unindexed leaf pool, regardless
# of age/color. Major, unknown/partially published or opaque domains refuse.
# No candidate word is dereferenced here. The optional extra typed C aliases
# are conservative words (never scanned as heap or used as object addresses).
from pcc.unsafe import (atomic_load_i32, atomic_load_i64, call_void_ptr2, global_addr, null, ptr_diff)
_old_epoch = extern("pcc_thread_world_stop_epoch_held", (), c_int64)
_old_gc = extern("pcc_gc_backend", (), c_int64)
_old_first = extern("pcc_gc_object_walk_first", (), c_ptr)
_old_next = extern("pcc_gc_object_walk_next", (c_ptr,), c_ptr)
_old_pending = extern("pcc_gc_alloc_stage_pending", (), c_int64)
_old_index = extern("pcc_gc_object_index_find_stable", (c_ptr,), c_ptr)
_old_slice = extern("pcc_gc_visit_object_slots_slice", (c_ptr,c_int64,c_int64,c_ptr,c_ptr,c_ptr), c_int64)
# Limit is a diagnostic refusal, never a truncated successful scan.
_OLD_LIMIT = 16777216


# The two emitter-owned direct entrypoints preserve ordinary registration vs
# old process-static registration semantics. This additional exact set alone
# is not a capability: a checked static image and no foreign exposures are
# required before the old cut uses it. No reads through a registered word.
from pcc.unsafe import (atomic_cas_i64, atomic_store_i64, define_global_i64,
                        define_global_i64_array, define_global_null_ptr_array,
                        int_to_ptr, logical_shift_right_i64, ptr_to_int)
_old_generic_register = extern("pcc_gc_pointer_register", (c_ptr,), c_int64)
_old_entry_register = extern("pcc_gc_pointer_register_static_literal", (c_ptr,), c_int64)
_exp2_old_scope_ok = extern("pcc_exp2_window_domain_ok", (), c_int64)
_old_total_mode_leases = extern("pcc_gc_foreign_lease_count_quiescent", (), c_int64)
_old_image_verified = extern("pcc_exp2_window_image_verified", (), c_int64)
define_global_null_ptr_array("pcc_exp2_old_origin_keys",32768)
define_global_i64("pcc_exp2_old_origin_poison",0)
define_global_i64_array("pcc_exp2_old_raw_metrics",0,0,0,0,0,0)


@c_abi_export("pcc_exp2_old_literal_slot")
def _old_literal_slot(bits:i64)->i64:
    return (logical_shift_right_i64(bits,3)^logical_shift_right_i64(bits,13))&32767


@c_abi_export("pcc_exp2_old_literal_record")
def _old_literal_record(w)->None:
    if ptr_is_null(w) or is_tagged_int(w):
        atomic_store_i64(global_addr("pcc_exp2_old_origin_poison"),0,1,"release")
        return
    bits:i64=ptr_to_int(w)
    at:i64=_old_literal_slot(bits)
    keys=global_addr("pcc_exp2_old_origin_keys")
    i:i64=0
    while i<128:
        found:i64=atomic_cas_i64(keys,at*8,0,bits,"acq_rel","acquire")
        if found==0 or found==bits:
            return
        at=(at+1)&32767
        i+=1
    # Bounded table can never silently drop an origin.
    atomic_store_i64(global_addr("pcc_exp2_old_origin_poison"),0,1,"release")


@c_abi_export("pcc_exp2_old_library_literal")
def pcc_exp2_old_library_literal(w)->i64:
    rc:i64=_old_generic_register(w)
    if rc>=0:
        _old_literal_record(w)
    else:
        atomic_store_i64(global_addr("pcc_exp2_old_origin_poison"),0,1,"release")
    return rc


@c_abi_export("pcc_exp2_old_entry_literal")
def pcc_exp2_old_entry_literal(w)->i64:
    rc:i64=_old_entry_register(w)
    if rc>=0:
        _old_literal_record(w)
    else:
        atomic_store_i64(global_addr("pcc_exp2_old_origin_poison"),0,1,"release")
    return rc


@c_abi_export("pcc_exp2_old_literal_known")
def _old_literal_known(w)->i64:
    bits:i64=ptr_to_int(w)
    at:i64=_old_literal_slot(bits)
    keys=global_addr("pcc_exp2_old_origin_keys")
    i:i64=0
    while i<128:
        found:i64=atomic_load_i64(keys,at*8,"acquire")
        if found==0:
            return 0
        if found==bits:
            return 1
        at=(at+1)&32767
        i+=1
    return 0


@c_abi_export("pcc_exp2_old_static_key")
def pcc_exp2_old_static_key(i:i64):
    # Raw diagnostic oracle for the separate calibration harness; no read
    # through the key and no authority to create an origin.
    if i<0 or i>=32768:
        return null()
    return int_to_ptr(atomic_load_i64(global_addr("pcc_exp2_old_origin_keys"),i*8,"acquire"))


@c_abi_export("pcc_exp2_old_exact_metric")
def pcc_exp2_old_exact_metric(i:i64)->i64:
    if i<0 or i>5:
        return -1
    return atomic_load_i64(global_addr("pcc_exp2_old_raw_metrics"),i*8,"acquire")


@c_abi_export("pcc_exp2_old_exact_only_status")
def _old_exact_only(audit)->i64:
    # Inspect only stopped address-index storage. A non-null bucket is a
    # word, NOT permission to inspect its target (including heap/ELF words).
    slots=global_load_ptr("pcc_py_gc_managed_pointer_slots")
    cap:i64=load_i64(global_addr("pcc_py_gc_managed_pointer_cap"),0)
    count:i64=load_i64(global_addr("pcc_py_gc_managed_pointer_count"),0)
    m=global_addr("pcc_exp2_old_raw_metrics")
    i:i64=0
    while i<6:
        atomic_store_i64(m,i*8,0,"release")
        i+=1
    atomic_store_i64(m,0,count,"release")
    if cap<0 or cap>_OLD_LIMIT or count<0 or count>cap or (cap>0 and ptr_is_null(slots)):
        store_i64(audit,24,-15)
        return -15
    if atomic_load_i64(global_addr("pcc_exp2_old_origin_poison"),0,"acquire")!=0:
        store_i64(audit,24,-16)
        return -16
    sealed:i64=_old_image_verified()
    i=0
    seen:i64=0
    origins:i64=0
    ascii_starts:i64=0
    indexed:i64=0
    status:i64=1
    while i<cap:
        word=load_ptr(slots,i*8)
        if ptr_is_null(word)==0:
            seen+=1
            if sealed==1 and _old_literal_known(word)!=0:
                origins+=1
            else:
                off:i64=ptr_diff(word,global_addr("pcc_ascii_image_chars"))
                if off>=0 and off<128*48 and off%48==0:
                    ascii_starts+=1
                elif ptr_is_null(_old_index(word))==0:
                    indexed+=1
                else:
                    # Distinguish a known but not-yet-sealed typed origin
                    # from an untyped unknown WITHOUT inspecting its header.
                    status=-14
                    if sealed!=1 and _old_literal_known(word)!=0:
                        status=-17
                    store_i64(audit,16,-2)
                    atomic_store_i64(m,40,1,"release")
                    break
        i+=1
    atomic_store_i64(m,8,seen,"release")
    atomic_store_i64(m,16,origins,"release")
    atomic_store_i64(m,24,ascii_starts,"release")
    atomic_store_i64(m,32,indexed,"release")
    if status==1 and seen!=count:
        status=-15
    if status!=1:
        store_i64(audit,24,status)
    return status



@c_abi_export("pcc_exp2_old_domain_status")
def _old_domain(epoch: i64) -> i64:
    if epoch <= 0 or _old_epoch() != epoch:
        return -1
    if _old_gc() != 3 or _exp2_old_scope_ok() != 1:
        return -2
    if _old_total_mode_leases() != 0:
        return -3
    if atomic_load_i32(global_addr("pcc_gc_mark_active"), 0, "acquire") != 0:
        return -4
    if (atomic_load_i32(global_addr("py_gc_collecting"), 0, "acquire") != 0
            or atomic_load_i32(global_addr("pcc_gc_explicit_collect_active"), 0, "acquire") != 0
            or atomic_load_i32(global_addr("pcc_gc_concurrent_sweep_state"), 0, "acquire") != 0
            or atomic_load_i64(global_addr("pcc_gc_scalar_sweep_state"), 0, "acquire") != 0):
        return -11
    if _old_pending() != 0:
        return -5
    return 1


@c_abi_export("pcc_exp2_old_count_fits")
def _old_count(n: i64, cap: i64, data) -> i64:
    if n < 0 or cap < 0 or n > cap or cap > _OLD_LIMIT or (n > 0 and ptr_is_null(data)):
        return 0
    return 1


@c_abi_export("pcc_exp2_old_verify_shape")
def _old_shape(o, tag: i64, size: i64, flags: i64) -> i64:
    detail: i64 = _old_shape_detail(o, tag, size, flags)
    return -7 if detail < -1000 else detail


@c_abi_export("pcc_exp2_old_verify_shape_detail")
def _old_shape_detail(o, tag: i64, size: i64, flags: i64) -> i64:
    # Preserve public verdicts. -1000 - clause is the private encoding for
    # the first failed layout guard. The stopped visitor converts it to -7.
    # 0: known no-reference scalar; 1: vetted layout; -6: opaque/unknown.
    if size < 16:
        return -1001
    if (flags & (524288 | 2048 | 131072)) != 0:
        return -1002
    if (tag == abi_constant("object.type.str") or tag == abi_constant("object.type.int")
            or tag == abi_constant("object.type.float") or tag == abi_constant("object.type.bool")
            or tag == abi_constant("object.type.none") or tag == abi_constant("object.type.complex")
            or tag == abi_constant("object.type.bytes") or tag == abi_constant("object.type.bytearray")
            or tag == 33 or tag == 34):
        return 0
    if tag == abi_constant("object.type.list"):
        if size < 40:
            return -1101
        if _old_count(load_i64(o,16),load_i64(o,24),load_ptr(o,32)) == 0:
            return -1102
    elif tag == abi_constant("object.type.tuple"):
        if size < 24:
            return -1103
        n: i64 = load_i64(o,16)
        if n < 0 or n > _OLD_LIMIT or n > (size - 24) // 8:
            return -1104
    elif tag == abi_constant("object.type.dict"):
        if size < 56:
            return -1105
        used: i64 = load_i64(o,48)
        if (_old_count(used, load_i64(o,24), load_ptr(o,40)) == 0
                or load_i64(o,16) < 0 or load_i64(o,16) > used):
            return -1106
    elif tag == abi_constant("object.type.set"):
        if size < 48:
            return -1107
        cap: i64 = load_i64(o,24)
        if (_old_count(cap, cap, load_ptr(o,40)) == 0 or load_i64(o,16) < 0
                or load_i64(o,16) > load_i64(o,32) or load_i64(o,32) > cap):
            return -1108
    elif tag == abi_constant("object.type.func"):
        if size < 96:
            return -1109
        # A C extension's PyCFunction may hide retained objects outside slots.
        if ptr_is_null(load_ptr(o,16)) == 0:
            return -6
    elif tag == abi_constant("object.type.class"):
        if size < 120:
            return -1110
        nb: i64 = load_i32(o,24)
        nm: i64 = load_i32(o,40)
        nt: i64 = load_i32(o,56)
        nf: i64 = load_i32(o,72)
        if _old_count(nb, _OLD_LIMIT, load_ptr(o,32)) == 0:
            return -1111
        if _old_count(nm, _OLD_LIMIT, load_ptr(o,48)) == 0:
            return -1112
        if _old_count(nt, _OLD_LIMIT, load_ptr(o,64)) == 0:
            return -1113
        if _old_count(nf, _OLD_LIMIT, load_ptr(o,80)) == 0:
            return -1114
    elif (tag == abi_constant("object.type.instance") or tag == abi_constant("object.type.valuebox")
            or (tag >= abi_constant("object.type.user_class_start") and tag < 65536)):
        if size < 24:
            return -1020
        cls = load_ptr(o,16)
        if ptr_is_null(cls):
            return -1021
        if is_tagged_int(cls):
            return -1022
        # Compare candidate value in the stopped registry before dereferencing.
        cn = _old_index(cls)
        if ptr_is_null(cn):
            return -1023
        if ptr_eq(load_ptr(cn,0),cls) == 0:
            return -1024
        if load_i64(cn,32) != 0:
            return -1025
        if load_i64(cn,8) < 120:
            return -1026
        if load_i32(cls,8) != abi_constant("object.type.class"):
            return -1027
        sz: i64 = load_i32(cls,88)
        fields: i64 = load_i32(cls,72)
        if sz < 24:
            return -1028
        if (sz & 7) != 0:
            return -1029
        if sz > size:
            return -1030
        if fields < 0:
            return -1031
        if fields >= _OLD_LIMIT:
            return -1032
        if fields + 1 > (size - 24) // 8:
            return -1033
        if (sz - 24) // 8 > _OLD_LIMIT:
            return -1034
    elif tag == abi_constant("object.type.iter"):
        if size < 32:
            return -1115
    elif tag == abi_constant("object.type.gen"):
        if size < 56:
            return -1116
        if (flags & abi_constant("object.gen.inline_frame_flag")) != 0:
            if size < 64:
                return -1117
            n = load_i64(o,abi_constant("object.gen.inline_length_offset"))
            if n < 0 or n > _OLD_LIMIT or n > (size-64)//8:
                return -1118
    elif tag == abi_constant("object.type.virtual_thread"):
        # Native scheduler timer, IO and wait-node backpointers do not own
        # objects; runnable and parked state requires the independent root cut.
        if size < 200:
            return -1119
    elif tag == abi_constant("object.type.continuation"):
        if size < 48:
            return -1120
        chunk = load_ptr(o,24)
        if ptr_is_null(chunk):
            return -1121
        n = load_i64(chunk,8)
        if _old_count(n, _OLD_LIMIT, load_ptr(chunk,16)) == 0 or n != load_i32(chunk,0):
            return -1122
    elif tag == abi_constant("object.type.vthread_channel"):
        if size < 40:
            return -1123
        kind: i64 = load_i64(o,16)
        if kind == 0:
            if size < 128:
                return -1124
            cap = load_i64(o,24)
            if cap < 0 or cap > 1048576 or cap > (size-128)//8 or load_i64(o,32)<0 or load_i64(o,32)>cap:
                return -1125
        elif kind != 1 and kind != 2:
            return -1126
    elif tag == abi_constant("object.type.coroutine"):
        if size < 64:
            return -1127
    elif tag == abi_constant("object.type.task"):
        if size < 48:
            return -1128
    elif tag == abi_constant("object.type.exc"):
        if size < 64:
            return -1129
        if (_old_count(load_i32(o,56), load_i32(o,60), load_ptr(o,48)) == 0):
            return -1130
    elif tag == abi_constant("object.type.property"):
        if size < 40:
            return -1131
    elif tag == abi_constant("object.type.classmethod") or tag == abi_constant("object.type.staticmethod"):
        if size < 24:
            return -1132
    else:
        # Weakrefs need logical death/clearing; memoryviews/raw foreign export
        # paths need lifetime leases; handles, extension, native thread/sync
        # and file domains can own opaque state. Never call their traverse.
        return -6
    return 1


# Shadow observer is separate from the runtime's trusted visitor. A failed
# old-census call never confers a new-page reclamation decision.
_exp2_old_observe = extern("pcc_exp2_process_old_reference", (c_ptr,c_int64), c_void)

@c_abi_export("pcc_exp2_old_deliver_slot")
def pcc_exp2_old_deliver_slot(slot, role:i64, state) -> None:
    audit = load_ptr(state,16)
    if ptr_is_null(slot) or (role != 1 and role != 2 and role != 3):
        store_i64(audit,24,-10)
        return
    if role == 3:
        # Conservative borrowed cache entries, not precise strong owners.
        store_i64(audit,40,load_i64(audit,40)+1)
    else:
        store_i64(audit,8,load_i64(audit,8)+1)
    value=load_ptr(slot,0)
    _exp2_old_observe(value,load_i64(state,24))
    call_void_ptr2(load_ptr(state,0),value,load_ptr(state,8))


@c_abi_export("pcc_exp2_old_alias")
def _old_alias(word,state) -> None:
    audit = load_ptr(state,16)
    store_i64(audit,48,load_i64(audit,48)+1)
    _exp2_old_observe(word,load_i64(state,24))
    call_void_ptr2(load_ptr(state,0),word,load_ptr(state,8))


@c_abi_export("pcc_exp2_old_borrowed_words")
def _old_aliases(o, tag: i64, state) -> None:
    # Inspect only *declared* borrowed C-string fields with validated array
    # lengths. Read pointer words from current storage; never chase a C string.
    if tag == abi_constant("object.type.func"):
        _old_alias(load_ptr(o,72),state)
    elif tag == abi_constant("object.type.coroutine"):
        _old_alias(load_ptr(o,16),state)
    elif tag == abi_constant("object.type.class"):
        _old_alias(load_ptr(o,16),state)
        i: i64 = 0
        methods = load_ptr(o,64)
        while i < load_i32(o,56):
            _old_alias(load_ptr(methods,i*abi_constant("object.class_method.size")),state)
            i += 1
        i = 0
        names = load_ptr(o,80)
        while i < load_i32(o,72):
            _old_alias(load_ptr(names,i*8),state)
            i += 1
    elif tag == abi_constant("object.type.exc"):
        frames=load_ptr(o,48)
        i: i64=0
        while i < load_i32(o,56):
            _old_alias(load_ptr(frames,i*32),state)
            _old_alias(load_ptr(frames,i*32+8),state)
            _old_alias(load_ptr(frames,i*32+16),state)
            i+=1


@c_abi_export("pcc_exp2_visit_stopped_old_edges")
def pcc_exp2_visit_stopped_old_edges(epoch:i64,visitor,context,audit)->i64:
    """Trusted collector word visitor only. No user callback, graph mutation
    or allocations. 1 means ALL registered old owners, not all process roots.
    audit[12]: owners, strong slots, first denied tag, status, scalar owners,
    update-only slots, typed conservative aliases, total nodes, failing bytes,
    failing flags, first failing shape clause, reserved.
    A negative is never a minor license.
    """
    if ptr_is_null(audit):
        return -1
    i:i64=0
    while i<12:
        store_i64(audit,i*8,0)
        i+=1
    store_i64(audit,16,-1)
    rc:i64=_old_domain(epoch)
    if rc!=1 or ptr_is_null(visitor):
        if rc==1:
            rc=-1
        store_i64(audit,24,rc)
        return rc
    exact_state:i64=_old_exact_only(audit)
    if exact_state!=1:
        return exact_state
    state=stack_alloc(32)
    store_ptr(state,0,visitor)
    store_ptr(state,8,context)
    store_ptr(state,16,audit)
    curs=stack_alloc(16)
    # First pass vet all registry owners before issuing the first callback.
    # Both passes execute in the same immutable stopped-world epoch.
    phase:i64=0
    while phase<2:
        node=_old_first()
        while ptr_is_null(node)==0:
            o=load_ptr(node,0)
            if load_i64(node,32)!=0 or ptr_is_null(o) or is_tagged_int(o):
                store_i64(audit,24,-8)
                return -8
            tag:i64=load_i32(o,abi_constant("object.header.type_tag_offset"))
            flags:i64=load_i32(o,abi_constant("object.header.flags_offset"))
            size:i64=load_i64(node,8)
            kind:i64=_old_shape_detail(o,tag,size,flags)
            if kind<0:
                if kind<-1000:
                    store_i64(audit,80,-1000-kind)
                    kind=-7
                store_i64(audit,16,tag)
                store_i64(audit,24,kind)
                store_i64(audit,64,size)
                store_i64(audit,72,flags)
                return kind
            if phase==0:
                store_i64(audit,56,load_i64(audit,56)+1)
                if kind==0:
                    store_i64(audit,32,load_i64(audit,32)+1)
                else:
                    store_i64(audit,0,load_i64(audit,0)+1)
            elif kind!=0:
                store_i64(state,24,tag)
                cursor:i64=0
                while True:
                    if _old_slice(o,cursor,64,pcc_exp2_old_deliver_slot,state,curs)!=1:
                        store_i64(audit,16,tag)
                        store_i64(audit,24,-10)
                        return -10
                    if load_i64(audit,24)<0:
                        store_i64(audit,16,tag)
                        return -10
                    nxt:i64=load_i64(curs,0)
                    if nxt==-1:
                        break
                    if nxt<=cursor or nxt>_OLD_LIMIT*4:
                        store_i64(audit,16,tag)
                        store_i64(audit,24,-9)
                        return -9
                    cursor=nxt
                _old_aliases(o,tag,state)
            node=_old_next(node)
        rc=_old_domain(epoch)
        if rc!=1:
            store_i64(audit,24,rc)
            return rc
        phase+=1
    store_i64(audit,24,1)
    return 1
