"""Source-relative compile observer for the ordinary-constructor native test.

No provisioning, source copying, alternative import roots or IR rewriting.
Both ordinary and static-definition constructors retain their real slot guards.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import sys
import struct


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def _static_constructor_contract(module, function, status, output_slot):
    """Prove the static descriptor and this result's exact rooted handoff."""
    from pcc.backend.self_backend_kernel import get_indexed_function_kernel
    from tests.python.root_slot_contract import RootSlotContract

    blocks = get_indexed_function_kernel(function).materialize_legacy_blocks(function)
    roots = RootSlotContract(blocks, module.globals_)
    matches = [(block, index, ins) for block, index, ins in roots.rows
               if roots.called(ins, "py_func_new_static_def")
               and ins.data[0] == status.lstrip("%")]
    require(len(matches) == 1, "Static constructor status lacks its exact parsed call")
    producer, position, invoke = matches[0]
    arguments = roots.args(invoke)
    require(len(arguments) == 9, "Static constructor ABI changed")
    output = roots.slot(arguments[-1])
    require(output == roots.slot(output_slot.lstrip("%")), "Static constructor output identity changed")
    frame = roots.require_owning(output, allow_lifo=True)[:2]
    descriptor, offset = roots.slot(arguments[2])
    global_ = roots.globals.get(descriptor.lstrip("@"))
    require(global_ is not None and global_.is_constant and offset == 0
            and global_.type.is_array and global_.type.elem is not None
            and global_.type.elem.is_ptr and global_.type.count > 0,
            "Static signature must be an immutable pointer descriptor")
    by_name = {block.name: block for block in blocks}
    successors = {}
    for block in blocks:
        term = block.terminator
        successors[block.name] = (
            tuple(term.data[:1]) if term.kind == "br" else
            tuple(term.data[1:]) if term.kind == "br_cond" else
            (term.data[2],) + tuple(target for _, target in term.data[3])
            if term.kind == "switch" else ()
        )

    def frame_event(ins, entering):
        names = (("pcc_gc_frame_enter", "pcc_gc_frame_enter_lifo") if entering
                 else ("pcc_gc_frame_leave", "pcc_gc_frame_leave_lifo"))
        return (any(roots.called(ins, name) for name in names)
                and roots.slot(roots.args(ins)[1 if entering else 0]) == frame)

    registrations = [ins for _, _, ins in roots.rows if frame_event(ins, True)]
    require(len(registrations) == 1, "Static output needs one frame registration")
    lifo = roots.called(registrations[0], "pcc_gc_frame_enter_lifo")
    leave_protocol = "pcc_gc_frame_leave_lifo" if lifo else "pcc_gc_frame_leave"

    # Module initializers also register process-lifetime GLOBAL roots. Audit
    # this constructor's local output frame, not those unrelated registrations.
    # Indexed instruction arenas project fresh objects: identify the producer
    # by its exact block/position, never Python object identity.
    pending, visited, joins, reached = [(blocks[0].name, False)], set(), {}, False
    while pending:
        name, active = pending.pop()
        if (name, active) in visited:
            continue
        visited.add((name, active))
        require(joins.get(name, active) == active, "Static output lifetime disagrees at CFG join")
        joins[name] = active
        for index, ins in enumerate(by_name[name].instructions):
            if frame_event(ins, True):
                require(not active, "Static output frame entered twice")
                active = True
            elif frame_event(ins, False):
                require(roots.called(ins, leave_protocol), "Static output frame retirement uses the wrong protocol")
                require(active, "Static output frame retired without registration")
                active = False
            if name == producer.name and index == position:
                require(active, "Static output must be registered before construction")
                reached = True
        if by_name[name].terminator.kind in ("ret", "ret_void"):
            require(not active, "Static output frame remains active at return")
        pending.extend((target, active) for target in successors[name])
    require(reached, "Static constructor is unreachable")

    def consumes(ins):
        return (roots.called(ins, "pcc_gc_take_pinned_slot")
                and roots.slot(roots.args(ins)[0]) == output)

    reaching = {block.name for block in blocks if any(consumes(ins) for ins in block.instructions)}
    require(reaching, "Static output has no exact take handoff")
    changed = True
    while changed:
        changed = False
        for name, targets in successors.items():
            if name != producer.name and name not in reaching and any(target in reaching for target in targets):
                reaching.add(name)
                changed = True
    pending = [(producer.name, position + 1, frozenset(), False)]
    paths = 0
    while pending:
        name, offset, seen, retired = pending.pop()
        require(name not in seen, "Static output handoff path loops before consumption")
        seen = seen | {name}
        for ins in by_name[name].instructions[offset:]:
            if (roots.called(ins, "pcc_gc_store_root") and roots.args(ins)[1] == "null"
                    and roots.slot(roots.args(ins)[0]) == output):
                require(False, "Static output cleared before its handoff")
            if ins.kind == "store" and ins.data[1] == "null" and roots.slot(ins.data[3]) == output:
                require(False, "Static output cleared before its handoff")
            if frame_event(ins, False):
                require(not retired, "Static output frame retired twice")
                retired = True
                continue
            if retired and ins.kind == "call":
                require(consumes(ins), "Static output frame retired before its final take")
            if consumes(ins):
                paths += 1
                break
        else:
            targets = [target for target in successors[name]
                       if target in reaching and target != producer.name]
            require(targets, "Static output never reaches its exact take")
            pending.extend((target, 0, seen, retired) for target in targets)
    require(paths > 0, "Static output has no successful handoff path")
    return {"descriptor": descriptor, "descriptor_words": global_.type.count,
            "output_frame": frame, "success_handoff_paths": paths}


def audit_caller_ir(text, path, require_persistent):
    """Apply test_callable_construction_codegen contracts to real link input.

    No _emit/probe/test compiler invocation occurs. The additional per-call flag
    check ties persistent ownership to the helper's actual output slot.
    """
    from pcc.backend.self_backend_parse import parse_self_backend_module
    from pcc.backend.self_backend_verify import verify_parsed_module
    module = parse_self_backend_module(text)
    verify_parsed_module(module)
    functions = {function.name: function for function in module.functions}
    require(not re.search(r"call [^\n]*@py_func_new_named\(", text), "Caller bypassed the slot constructor")
    rows = []
    for definition in re.finditer(r"^define[^\n]*\{\n.*?^}", text, re.M | re.S):
        body = definition.group(0)
        symbol = re.search(r'@(?:"([^"]+)"|([^ (]+))\(', body.splitlines()[0])
        require(symbol is not None, "Missing caller symbol")
        symbol = symbol.group(1) or symbol.group(2)
        aliases = dict(re.findall(r"(%[^ ]+) = bitcast ptr (%[^ ]+) to ptr", body))

        def resolve(value):
            visited = set()
            while value in aliases:
                require(value not in visited, "Cyclic pointer alias")
                visited.add(value)
                value = aliases[value]
            return value

        # ownership_lowering._ensure_owned_local_flag records slot/flag object
        # identities. Its _patch_fn_err_exit_gc_root_leave emits the matching
        # conditional retirement at lines 1269-1282. Recover that actual dataflow
        # rather than concatenating independently uniquified LLVM value names.
        flag_allocas = set(re.findall(r"(?m)^\s*(%\S+) = alloca i1\b", body))
        flag_loads = {
            result: resolve(flag)
            for result, flag in re.findall(r"(?m)^\s*(%\S+) = load i1, ptr (%[^,\s]+)", body)
        }
        owned_slot_flags = {}
        for select in re.finditer(
            r"(?m)^\s*(%\S+) = select i1 (%[^,\s]+), ptr (%[^,\s]+), ptr (%[^,\s]+)\n", body
        ):
            active, condition, slot, empty = select.groups()
            flag = flag_loads.get(condition)
            if flag not in flag_allocas:
                continue
            after_select = body[select.end():]
            retirement = re.match(
                r"\s*store i1 0, ptr " + re.escape(flag) + r"(?:, [^\n]+)?\n"
                r"\s*call void[^\n]*@pcc_gc_store_root\(ptr " + re.escape(active) + r", ptr null\)",
                after_select,
            )
            if retirement is None:
                continue
            slot = resolve(slot)
            empty = resolve(empty)
            if not re.search(r"store ptr null, ptr " + re.escape(empty) + r"(?:,|\n)", body):
                continue
            owned_slot_flags.setdefault(slot, []).append({
                "flag": flag, "flag_load": condition, "active_slot": active,
                "output_slot": slot, "empty_slot": empty,
                "selection": select.group(0).strip(),
                "retirement": retirement.group(0).strip(),
                "source_contract": "ownership_lowering.py:_patch_fn_err_exit_gc_root_leave",
            })

        calls = list(re.finditer(
            r"(?m)^\s*(%\S+) = call i64[^\n]*@(py_func_new_signature_slots|py_func_new_static_def)\(([^\n]+)\)\n", body))
        for call in calls:
            tail = body[call.end():]
            first = tail.splitlines()[0].strip()
            require(first.startswith(("store i1 1, ptr ", "%")), "Unexpected first constructor successor: " + first)
            check = re.search(r"(%\S+) = icmp slt i64 " + re.escape(call.group(1)) + r", 0\n", tail)
            require(check is not None, "Constructor status has no signed-negative check")
            prefix = tail[:check.start()]
            require("call " not in prefix and "br " not in prefix, "Callback or failure edge before ownership/status check")
            branch = re.match(r"\s*br i1 " + re.escape(check.group(1))
                              + r", label (%[^,\s]+), label (%[^\s]+)", tail[check.end():])
            require(branch is not None, "Constructor status does not directly select its failure edge")
            helper = call.group(2)
            arguments = call.group(3)
            inputs = re.match(r"ptr (%[^ ,]+), ptr (%[^ ,]+),", arguments) if helper == "py_func_new_signature_slots" else None
            output = re.search(r", ptr (%[^ ,]+)$", arguments)
            require(output is not None and (helper == "py_func_new_static_def" or inputs is not None),
                    "Unexpected constructor slot ABI")
            output_slot = resolve(output.group(1))
            flag_evidence = owned_slot_flags.get(output_slot, [])
            matching_flags = {row["flag"] for row in flag_evidence}
            require(len(matching_flags) <= 1, "Ambiguous persistent ownership for constructor output")
            owned_flag = next(iter(matching_flags), None)
            persistent = owned_flag is not None
            first_flag_store = re.match(r"store i1 1, ptr (%[^,\s]+)(?:,.*)?$", first)
            if first_flag_store is not None:
                require(persistent and resolve(first_flag_store.group(1)) == owned_flag,
                        "Immediate owned flag does not control retirement of the constructor output")
            if persistent:
                require(first_flag_store is not None and resolve(first_flag_store.group(1)) == owned_flag,
                        "Persistent output's matching owned flag is not set immediately after the helper")
            if "__gen_resume" in symbol:
                require(persistent, "Generator constructor output lacks its persistent owned flag")
            static_contract = None
            if helper == "py_func_new_static_def":
                static_contract = _static_constructor_contract(
                    module, functions[symbol], call.group(1), output_slot,
                )
            else:
                metadata = re.search(r"call i64[^\n]*@py_func_init_metadata_slots\(ptr (%[^ ,]+),", tail)
                require(metadata is not None and resolve(metadata.group(1)) == output_slot,
                        "Constructor output did not reach slot metadata initialization")
                retired = {
                    resolve(value) for value in re.findall(
                        r"call void[^\n]*@pcc_gc_store_root\(ptr (%[^ ,]+), ptr null\)", tail[:metadata.start()])
                }
                require({resolve(inputs.group(1)), resolve(inputs.group(2))} <= retired,
                        "Constructor inputs not retired before metadata initialization")
            rows.append({
                "function": symbol, "actual_linker_input": str(path),
                "definition_sha256": hashlib.sha256(body.encode()).hexdigest(),
                "status": call.group(1), "output_slot": output_slot,
                "persistent": persistent, "owned_flag": owned_flag if persistent else None,
                "owned_flag_association": flag_evidence,
                "first_successor": first, "negative_status_failure_label": branch.group(1),
                "status_ready_label": branch.group(2),
                "constructor_helper": helper, "static_contract": static_contract,
                "metadata_initialization": "runtime_static_constructor" if static_contract else "caller_slot_helper",
                "inputs_retired_before_metadata": True if inputs is not None else None,
                "no_callback_or_branch_before_status_check": True,
            })
    if require_persistent:
        require(any(row["persistent"] for row in rows), "Lifetime fixture did not exercise persistent constructor output")
    return rows


def _save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _same_source_imports(root):
    loaded = {}
    for name, module in tuple(sys.modules.items()):
        if name.split(".", 1)[0] in ("pcc", "tests", "scripts"):
            filename = getattr(module, "__file__", None)
            if filename:
                path = Path(filename).resolve(strict=True)
                require(path.is_relative_to(root), "Mixed source import: " + name + " = " + str(path))
                loaded[name] = {"path": str(path), "sha256": _sha(path)}
    return loaded


def _static_elf64(data):
    require(len(data) >= 64 and data[:5] == b"\x7fELF\x02" and data[5] in (1, 2),
            "Linux native evidence requires an ELF64 header")
    endian = "<" if data[5] == 1 else ">"
    image_type, machine = struct.unpack_from(endian + "HH", data, 16)
    entry, phoff = struct.unpack_from(endian + "QQ", data, 24)
    ehsize, phentsize, phnum = struct.unpack_from(endian + "HHH", data, 52)
    require(image_type in (2, 3) and ehsize == 64 and phentsize == 56,
            "Unsupported ELF64 executable header")
    require(0 < phnum < 65535 and 64 <= phoff <= len(data)
            and phoff + phnum * phentsize <= len(data), "Invalid ELF64 program-header bounds")
    headers = []
    executable_entry = False
    for index in range(phnum):
        kind, flags, offset, address, _, filesz, memsz, alignment = struct.unpack_from(
            endian + "IIQQQQQQ", data, phoff + index * phentsize,
        )
        require(kind not in (2, 3), "Linux native binary has PT_DYNAMIC or PT_INTERP")
        require(offset + filesz <= len(data), "ELF64 segment exceeds file bounds")
        if kind == 1:
            require(filesz <= memsz, "ELF64 load segment exceeds memory size")
            executable_entry |= bool(flags & 1 and address <= entry < address + memsz)
        headers.append({"type": kind, "flags": flags, "offset": offset,
                        "file_bytes": filesz, "memory_bytes": memsz, "alignment": alignment})
    require(executable_entry, "ELF64 entry is not inside an executable PT_LOAD")
    return {"static": True, "machine": machine, "entry": entry, "program_headers": headers}


def main():
    source, binary, archive, directory = (Path(value).absolute() for value in sys.argv[1:5])
    require_persistent = sys.argv[5] == "1"
    root = Path(__file__).resolve().parents[2]
    receipt_path = directory / "compile-receipt.json"
    receipt = {
        "status": "ADMITTING", "compiler": "host-pcc0", "native_pcc1": "UNRUN",
        "source": str(source), "source_sha256": _sha(source),
        "runtime_archive": str(archive), "helper_sha256": _sha(Path(__file__)),
        "backend": "self", "libpython_mode": "off", "ir_scaffold_mode": "on",
        "link_inputs": [], "constructor_calls": [], "link_call_count": 0,
    }
    _save(receipt_path, receipt)
    stage = "ADMISSION"
    try:
        require(not os.environ.get("PCC_TEST_COMPILER"), "Host-pcc0 observer cannot audit native-pcc1 routing")
        require(source == source.resolve(strict=True), "Fixture must not be relocated through a symlink")
        require(source.parent == root / "tests/fixtures/native/constructor", "Compile the real repository fixture")
        require(os.environ.get("PCC_TEST_NO_NATIVE_PROVISIONING") == "1", "Native provisioning must be disabled")
        require(os.environ.get("PCC_NO_AUTO_PCC1") == "1", "pcc1 auto-provisioning must be disabled")
        require(os.environ.get("PCC_REFCOUNT_KIND") == "atomic", "Expected atomic runtime configuration")
        from tests.runtime_fixture_provenance import _verified_test_runtime_archive
        from pcc.driver.bootstrap_cache_identity import bootstrap_source_sha256
        from pcc.frontends.python import pipeline, pipeline_targets
        from pcc.tools.runtime_archive_provenance import codegen_checksum
        from scripts.verify_nolibpython import linked_libraries

        receipt["imports_before"] = _same_source_imports(root)
        admitted, manifest = _verified_test_runtime_archive(archive, threads=True)
        require(admitted == archive.resolve(strict=True), "Runtime selection changed during admission")
        provenance_path = Path(str(admitted) + ".provenance.json")
        capi_path = Path(str(admitted) + ".capi_syms")
        target = pipeline_targets.host_target_triple()
        receipt.update(
            runtime_archive=str(admitted), runtime_sha256=_sha(admitted),
            runtime_provenance_sha256=_sha(provenance_path), runtime_manifest=manifest,
            runtime_capi_sha256=_sha(capi_path), target_triple=target,
            frontend_source_sha256=bootstrap_source_sha256(root),
            compiler_codegen_checksum=codegen_checksum(), status="COMPILING",
        )
        require(re.fullmatch(r"[0-9a-f]{64}", receipt["compiler_codegen_checksum"]) is not None,
                "Unknown compiler identity cannot admit native evidence")
        _save(receipt_path, receipt)
        original_link = pipeline._link_with_self_backend_ir_texts

        def observe_link(ir_texts, *args, **kwargs):
            nonlocal stage
            stage = "LINK_INPUT_AUDIT"
            receipt["link_call_count"] += 1
            _save(receipt_path, receipt)
            require(receipt["link_call_count"] == 1, "Expected one actual linker call")
            actual = inspect.signature(original_link).bind(ir_texts, *args, **kwargs)
            actual.apply_defaults()
            values = actual.arguments
            require(values["ir_texts"] is ir_texts, "IR identity changed")
            require(Path(values["out_path"]).resolve() == binary.resolve(), "Linker output path changed")
            require(Path(values["runtime_archive"]).resolve(strict=True) == admitted, "Linker runtime changed")
            require(not values["extra_link_inputs"] and not values["extra_link_args"],
                    "Unexpected extra linker inputs or arguments")
            receipt["actual_link_call"] = {
                key: values[key] for key in ("out_path", "runtime_archive", "verbose",
                    "needs_libpython", "needs_native_extension_exports", "extra_link_inputs",
                    "extra_link_args", "tmp_dir", "consume_ir_texts")
            }
            _save(receipt_path, receipt)
            require(isinstance(ir_texts, list) and all(isinstance(text, str) for text in ir_texts),
                    "Expected the actual list of linker IR strings")
            require(not kwargs.get("needs_libpython", False), "Link requested libpython")
            rows = []
            for text in ir_texts:
                path = directory / ("link-input-" + str(len(receipt["link_inputs"])) + ".ll")
                path.write_bytes(text.encode("utf-8"))
                receipt["link_inputs"].append({"path": str(path), "sha256": _sha(path)})
                _save(receipt_path, receipt)
                # The shared guard validates the real IR and every generator
                # output's own persistent-flag association. Aggregate nonvacuity
                # is checked below because dependency modules may have no calls.
                rows.extend(audit_caller_ir(text, path, False))
            require(rows, "Actual linker input contains no ordinary constructor calls")
            if require_persistent:
                require(any(row["persistent"] and "__gen_resume" in row["function"] for row in rows),
                        "Generator fixture did not exercise a persistent generator constructor output")
            receipt["constructor_calls"].extend(rows)
            receipt["status"] = "LINKING"
            _save(receipt_path, receipt)
            stage = "LINK"
            # Pass the SAME list object, positional objects and keyword values.
            # The real linker may consume its list; never substitute copies/IR.
            return original_link(ir_texts, *args, **kwargs)

        pipeline._link_with_self_backend_ir_texts = observe_link
        stage = "COMPILE"
        try:
            pipeline.compile_python(
                str(source), str(binary), backend="self", libpython_mode="off",
                ir_scaffold_mode="on", runtime_archive=str(admitted), target_triple=target,
            )
        finally:
            pipeline._link_with_self_backend_ir_texts = original_link
        stage = "BINARY_VERIFICATION"
        require(receipt["link_inputs"] and receipt["constructor_calls"], "Compiler bypassed the audited linker path")
        data = binary.read_bytes()
        magic = data[:4]
        require(magic in (b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf")
                or magic[:2] == b"MZ", "Compiler emitted a non-native file: " + repr(magic))
        if sys.platform.startswith("linux"):
            receipt["elf64"] = _static_elf64(data)
        libraries = linked_libraries(data)
        require(not any("libpython" in library.lower() or re.search(r"python[0-9].*\.dll", library.lower())
                        for library in libraries), "Binary imports a Python runtime: " + repr(libraries))
        receipt.update(binary_sha256=hashlib.sha256(data).hexdigest(),
                       binary_magic_hex=magic.hex(), linked_libraries=libraries,
                       imports_after=_same_source_imports(root))
        require(_sha(source) == receipt["source_sha256"], "Fixture changed during compilation")
        require(_sha(admitted) == receipt["runtime_sha256"], "Runtime changed during compilation")
        require(_sha(provenance_path) == receipt["runtime_provenance_sha256"], "Runtime provenance changed")
        require(_sha(capi_path) == receipt["runtime_capi_sha256"], "Runtime C-API inventory changed")
        _verified_test_runtime_archive(admitted, threads=True)
        for row in receipt["link_inputs"]:
            require(_sha(Path(row["path"])) == row["sha256"], "Retained linker IR changed")
        require(bootstrap_source_sha256(root) == receipt["frontend_source_sha256"], "Compiler source changed")
        for name, before in receipt["imports_before"].items():
            require(receipt["imports_after"].get(name) == before, "Imported source changed: " + name)
        receipt["status"] = "READY"
    except BaseException as error:
        receipt.update(status=stage + "_FAILED", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        _save(receipt_path, receipt)


if __name__ == "__main__":
    main()
