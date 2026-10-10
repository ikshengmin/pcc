"""Public eight-module strict closure and real host-target object emission."""

import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

import pytest


MODULES = tuple("pcc.frontends.python." + name for name in (
    "worker_resource_plan", "pipeline_frontend_workers",
    "pipeline_frontend_indexed_stage", "pipeline_indexed_handoff",
    "worker_process_pool",
)) + ("pcc.backend.self_backend_aarch64_darwin_regalloc",
      "pcc.backend.owned_elf_inputs", "pcc.backend.elf_x86_64")
SECTION = re.compile(r"^; ---- module: ([A-Za-z_][\w.]*) ----$", re.M)
DEFINITION = re.compile(r'^define[^\n]*@"?([A-Za-z_][\w.$]*)"?\(', re.M)


def require_definition(text, symbol):
    pattern = (r'^define[^\n]*@"?' + re.escape(symbol)
               + r'"?\([^\n]*\)[^\n]*\{\n(?P<body>.*?)^\}')
    matches = list(re.finditer(pattern, text, re.M | re.S))
    assert len(matches) == 1, (symbol, "missing/duplicate definition")
    body = matches[0].group("body")
    assert not re.search(r"^define\b", body, re.M), (symbol, "cross-function match")
    assert "strict.nolib" not in body and "@py_cpy_" not in body and '@"py_cpy_' not in body
    assert not re.search(r'@"?py_exc_new"?\(\s*i64\s+11\s*,', body), symbol
    assert "NotImplementedError" not in body, symbol
    assert re.search(r"\bcall\b", body), (symbol, "empty/return-only replacement")


def require_object_symbols(payload, target, required):
    """Owned readers validate the emitted container and every required code symbol."""
    darwin, windows = "-apple-" in target, "-windows-" in target
    if darwin:
        from pcc.backend.native_object import decode_native_object
        obj = decode_native_object(payload)
        machine = "PCO has no machine field; verified PIDX target and Darwin emitter route"
    elif windows:
        from pcc.backend.coff_x86_64 import parse_object
        obj = parse_object(payload)  # The reader requires AMD64 machine 0x8664.
        machine = 0x8664
    else:
        from pcc.backend.elf_x86_64 import parse_relocatable
        obj = parse_relocatable(payload)
        machine = 62 if target.startswith("x86_64-") else 183
        assert obj.machine == machine, "wrong ELF machine"
    for name in required:
        symbols = [symbol for symbol in obj.symbols if symbol.name == ("_" if darwin else "") + name]
        assert len(symbols) == 1, (name, "missing/duplicate object definition")
        symbol = symbols[0]
        index = symbol.section if windows else symbol.section_index
        assert 1 <= index <= len(obj.sections), (name, "undefined object symbol")
        section = obj.sections[index - 1]
        offset = symbol.offset if darwin else symbol.value
        assert 0 <= offset < len(section.data), (name, "symbol outside code")
        if darwin:
            assert symbol.external and (section.segname, section.sectname) == ("__TEXT", "__text")
        elif windows:
            assert symbol.external and symbol.function and section.flags & 0x20000000
        else:
            assert symbol.binding == 1 and symbol.type == 2 and section.flags & 4
    return {"machine": machine, "required_defined_symbols": list(required)}


@pytest.mark.integration
def test_worker_size_prior_modules_strict_target_emission(tmp_path, monkeypatch):
    from pcc.backend.self_backend_indexed_codec import encode_indexed_module_file
    from pcc.backend.self_backend_indexed_emit import emit_indexed_module_file
    from pcc.frontends.python.pipeline import compile_python_multi
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from pcc.tools.runtime_archive_provenance import codegen_checksum
    from tests.owned_ir_validation import verify_ir_text

    root = Path(__file__).resolve().parents[2]
    sources = [root / (name.replace(".", "/") + ".py") for name in MODULES]
    digests = {name: hashlib.sha256(path.read_bytes()).hexdigest()
               for name, path in zip(MODULES, sources)}
    target = host_target_triple()
    assert target.startswith(("arm64-apple-", "aarch64-apple-", "aarch64-unknown-linux-",
                              "arm64-unknown-linux-", "x86_64-unknown-linux-", "x86_64-pc-windows-")), target
    # Ordinary public closure construction with no fake exports and no old PIDX.
    monkeypatch.setenv("PCC_PY_FRONTEND_JOBS", "1")
    monkeypatch.setenv("PCC_PYTHON_IR_PASS_JOBS", "1")
    for name in ("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "PCC_DIRECT_INDEXED_KERNEL_EMIT",
                 "PCC_HOST_INDEXED_PROCESS_SPLIT"):
        monkeypatch.setenv(name, "0")
    monkeypatch.delenv("PCC_PYTHON_IR_PASSES", raising=False)
    monkeypatch.delenv("PCC_PYTHON_IR_PASS_SKIP_MODULE_PREFIXES", raising=False)
    receipt = {"status": "RUNNING", "target": target, "sources": digests,
               "codegen_sha256": codegen_checksum(), "modules": {},
               "scope": "public strict full closure and eight actual target objects",
               "native_execution": False}
    output = tmp_path / "closed-world.ll"
    try:
        compile_python_multi(
            [str(path) for path in sources], str(output), module_names=list(MODULES),
            entry_module=MODULES[0], emit_llvm_only=True, target_triple=target,
            libpython_mode="off", ir_scaffold_mode="on", backend="self",
            recursive_stdlib=False,
        )
        text = output.read_text()
        assert "strict.nolib.stub" not in text
        assert not re.search(r'\b(?:call|invoke)\b[^\n]*@"?py_cpy_', text)
        markers = list(SECTION.finditer(text))
        names = [match.group(1) for match in markers]
        assert len(names) == len(set(names)) and set(MODULES) <= set(names)
        sections = {match.group(1): text[match.end():markers[index + 1].start()
                    if index + 1 < len(markers) else len(text)]
                    for index, match in enumerate(markers)}
        whole = verify_ir_text(text)
        assert Counter(function.name for function in whole.functions) == Counter(DEFINITION.findall(text))
        receipt["closure_modules"] = names
        receipt["closure_definitions"] = len(whole.functions)
        del whole
        for name, source in zip(MODULES, sources):
            declared = [node.name for node in ast.parse(source.read_bytes()).body
                        if isinstance(node, ast.FunctionDef)]
            assert declared and len(declared) == len(set(declared))
            section = sections[name]
            required = ["user_" + name.replace(".", "_") + "_" + function for function in declared]
            for symbol in required:
                require_definition(section, symbol)
            module = verify_ir_text(section)
            assert module.triple == target
            assert Counter(function.name for function in module.functions) == Counter(DEFINITION.findall(section))
            assert all(function.indexed_kernel is not None for function in module.functions)
            sidecar, obj = tmp_path / (name + ".pidx"), tmp_path / (name + ".pco")
            encode_indexed_module_file(str(sidecar), module)
            emit_indexed_module_file(str(sidecar), str(obj), "PCO", optimize=False)
            assert obj.stat().st_size > 0 and not Path(str(obj) + ".tmp").exists()
            object_contract = require_object_symbols(obj.read_bytes(), target, required)
            receipt["modules"][name] = {"object_contract": object_contract, "functions": declared, "ir_sha256": hashlib.sha256(section.encode()).hexdigest(),
                                       "object_sha256": hashlib.sha256(obj.read_bytes()).hexdigest(),
                                       "object_bytes": obj.stat().st_size}
            del module
        assert digests == {name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for name, path in zip(MODULES, sources)}
        receipt["status"] = "PASS"
    except BaseException as error:
        receipt.update(status="FAIL", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        (tmp_path / "worker-size-prior-closed-world.json").write_text(json.dumps(receipt, indent=2) + "\n")
