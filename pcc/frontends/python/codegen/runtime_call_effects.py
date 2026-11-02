"""Audited runtime call effects used by both definitions and their callers.

No-safepoint entries must lower to a call-free, acyclic scalar function.
Readonly alone does not establish this contract: an implicit thread poll is
a call and may stop the mutator. Keep this admission intentionally narrow.
"""
from pcc.frontends.python.codegen.errors import L1CodegenError


NO_SAFEPOINT_RUNTIME_FUNCTIONS = frozenset({"py_tuple_len"})
_BOUNDED_LEAF_OPS = frozenset({
    "alloca", "load", "store", "getelementptr", "ptrtoint", "inttoptr",
    "bitcast", "icmp", "fcmp", "select", "phi", "trunc", "zext", "sext",
    "add", "sub", "and", "or", "xor", "shl", "lshr", "ashr",
    "br", "ret", "unreachable",
})


def verify_no_safepoint_runtime_function(function):
    if function.name not in NO_SAFEPOINT_RUNTIME_FUNCTIONS:
        return
    if str(function.function_type.return_type) not in (
        "void", "i1", "i8", "i16", "i32", "i64", "float", "double",
    ):
        raise L1CodegenError("no-safepoint runtime leaf must return a scalar: " + function.name)
    blocks = list(function.blocks)
    if not blocks:
        raise L1CodegenError("no-safepoint runtime definition has no body: " + function.name)
    successors = {block.name: [] for block in blocks}
    incoming = {block.name: 0 for block in blocks}
    for block in blocks:
        for instruction in block.instructions:
            # Text is also available across native InstructionRecord
            # boundaries where the cached opcode field is not exposed.
            text = str(instruction).strip()
            if text.startswith("%") and " = " in text:
                text = text.split(" = ", 1)[1]
            if ("call " in text or text.startswith("invoke ")
                    or text.startswith("callbr ")):
                raise L1CodegenError("no-safepoint runtime leaf contains a call: "
                                     + function.name + ": " + text)
            if text.startswith(("switch ", "indirectbr ", "resume ", "catch")):
                raise L1CodegenError("unproved control flow in no-safepoint runtime leaf: "
                                     + function.name)
            if not text or text.split()[0] not in _BOUNDED_LEAF_OPS:
                raise L1CodegenError("unproved instruction in no-safepoint runtime leaf: "
                                     + function.name + ": " + text)
            if text.startswith("alloca "):
                parts = text[7:].split(",")
                allocated = parts[0].strip()
                scalar = allocated in ("ptr", "i1", "i8", "i16", "i32", "i64", "float", "double")
                if ((not scalar and not allocated.endswith("*"))
                        or any(not part.strip().startswith("align ") for part in parts[1:])):
                    raise L1CodegenError("unbounded alloca in no-safepoint runtime leaf: " + function.name)
            # Wide/vector operations can acquire backend helper calls even
            # when their frontend instruction is not spelled as a call.
            words = text.replace(",", " ").replace("(", " ").replace(")", " ").split()
            if ("<" in text or any(word in ("fp128", "x86_fp80", "ppc_fp128")
                                    or word.startswith("i") and word[1:].isdigit() and int(word[1:]) > 64
                                    for word in words)):
                raise L1CodegenError("unproved wide operation in no-safepoint runtime leaf: " + function.name)
            if text.startswith("br "):
                for part in text.split("label %")[1:]:
                    target = part.split()[0].rstrip(",]").strip('"')
                    if target not in incoming:
                        raise L1CodegenError("unknown branch in no-safepoint runtime leaf: "
                                             + function.name)
                    successors[block.name].append(target)
                    incoming[target] += 1
    ready = [name for name in incoming if incoming[name] == 0]
    visited = 0
    while visited < len(ready):
        name = ready[visited]
        visited += 1
        for target in successors[name]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    if visited != len(blocks):
        raise L1CodegenError("cycle in no-safepoint runtime leaf: " + function.name)
