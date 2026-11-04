"""Exact compiler-owned stack-frame provenance and paired registration ABI."""
from pcc.ir.compat import ir


def record_private_root_address(host, slot, root_capacity=0):
    function = host.current_function
    try:
        addresses = host._private_root_addresses
    except AttributeError:
        addresses = {}
        host._private_root_addresses = addresses
    addresses.setdefault(function.name, {})[id(slot)] = (function, slot, root_capacity)


def record_root_pointer_alias(host, alias, source):
    if alias is source:
        return
    try:
        aliases = host._private_root_pointer_aliases
    except AttributeError:
        aliases = {}
        host._private_root_pointer_aliases = aliases
    aliases[id(alias)] = (alias, source)


def _uncast(host, value):
    try:
        aliases = host._private_root_pointer_aliases
    except AttributeError:
        return value
    seen = set()
    while id(value) in aliases:
        if id(value) in seen:
            return None
        seen.add(id(value))
        alias, source = aliases[id(value)]
        if alias is not value:
            return None
        value = source
    return value


def private_frame_scope(host, symbol, value, frame_map=None):
    """Prove compiler-owned storage, map bounds and a paired native lifetime.

    Only the three root-scope emitters supply frame_map. Allocation records
    carry capacity from the original allocation type, not pointer attributes
    or runtime address ranges. Ordinary scopes use all-exit cleanup; lexical
    scopes use their explicit enter/leave cleanup. This includes resume-body
    stack cells: persistent VALUES are saved to the heap frame before native
    return, while these physical registrations end with the activation.
    """
    if getattr(host, "_freestanding_module", False):
        return False
    function = getattr(host, "current_function", None)
    slot = _uncast(host, value)
    if function is None or slot is None:
        return False
    addresses = getattr(host, "_private_root_addresses", {}).get(function.name, {})
    proof = addresses.get(id(slot))
    if proof is None or proof[0] is not function or proof[1] is not slot:
        return False
    try:
        scopes = host._private_frame_scopes
    except AttributeError:
        scopes = {}
        host._private_frame_scopes = scopes
    table = scopes.setdefault(function.name, {})
    kind = 2 if "lifo" in symbol else 1
    record = table.get(id(slot))
    exact = record is not None and record[0] is function and record[1] is slot
    if not symbol.startswith("pcc_gc_frame_enter"):
        return exact and (record[2] & kind) != 0
    if len(proof) < 3 or proof[2] <= 0:
        return False
    if not isinstance(frame_map, ir.GlobalVariable) or not frame_map.global_constant:
        return False
    if host.module.globals.get(frame_map.name) is not frame_map:
        return False
    if not isinstance(frame_map.value_type, ir.IntType) or frame_map.value_type.width != 32:
        return False
    initializer = frame_map.initializer
    if not isinstance(initializer, ir.Constant) or not isinstance(initializer.type, ir.IntType) or initializer.type.width != 32:
        return False
    count = initializer.value
    if not isinstance(count, int):
        return False
    extent = -count if count < 0 else count
    if extent <= 0 or extent > 100000 or extent > proof[2]:
        return False
    if not exact:
        record = [function, slot, 0, {}, {}]
        table[id(slot)] = record
    record[2] |= kind
    record[4][kind] = (frame_map, count, proof[2])
    return True


def paired_private_frame_symbol(host, original, value, proposed):
    """Keep the actual local/mapped choice identical at every matching exit."""
    function = getattr(host, "current_function", None)
    slot = _uncast(host, value)
    if function is None or slot is None:
        return proposed
    table = getattr(host, "_private_frame_scopes", {}).get(function.name, {})
    record = table.get(id(slot))
    kind = 2 if "lifo" in original else 1
    if record is None or record[0] is not function or record[1] is not slot or (record[2] & kind) == 0:
        return proposed
    if original.startswith("pcc_gc_frame_enter"):
        if "_local" not in proposed:
            return proposed
        record[3][kind] = proposed
        return proposed
    entered = record[3].get(kind)
    if entered is None:
        return proposed
    return entered.replace("_enter", "_leave", 1)
