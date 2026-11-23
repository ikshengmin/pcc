"""Owned IR Python helpers publish results before direct-argument cleanup."""
from pathlib import Path
import re

import pytest

from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.codegen.errors import L1CodegenError
from pcc.frontends.python.pipeline_context import build_closed_world_context
from pcc.frontends.python.type_infer import infer_module


@pytest.mark.parametrize("expression,suffix", [
    ("ir.IntType(64)", "scaffold_IntType"),
    ("ir.Constant(ir.IntType(64), 12)", "scaffold_Constant_i64"),
    ("[ir.Constant(ir.IntType(64), 12)]", "scaffold_Constant_i64"),
    ("ir.ArrayType(ir.IntType(8), 7)", "scaffold_ArrayType"),
    ("builder.add(operand, operand)", "IRBuilder_add"),
    ("builder.fcmp_ordered('==', operand, operand)", "IRBuilder_fcmp_ordered"),
])
@pytest.mark.parametrize("contextual", [False, True])
def test_scaffold_object_result_is_published_at_return(tmp_path, monkeypatch, expression, suffix, contextual):
    _assert_scaffold_object_result_is_published_at_return(
        tmp_path, monkeypatch, expression, suffix, contextual,
    )
    if suffix == "scaffold_IntType":
        # Preserve the existing collection inventory while covering a real
        # None-payload constructor as both direct and container slot operands.
        for none_expression in (
            "ir.Constant(ir.IntType(64), None)",
            "[ir.Constant(ir.IntType(64), None)]",
        ):
            _assert_scaffold_object_result_is_published_at_return(
                tmp_path, monkeypatch, none_expression,
                "scaffold_Constant_none", contextual,
            )
        _assert_scaffold_object_result_is_published_at_return(
            tmp_path, monkeypatch, "ir.Constant(ir.IntType(64), None)",
            "scaffold_Constant_none", contextual, caller_sink=False,
        )
    if suffix == "scaffold_IntType":
        _assert_constant_obj_result_sinks(tmp_path, monkeypatch, contextual)
    if suffix == "IRBuilder_add":
        _assert_builder_call_result_sinks(tmp_path, monkeypatch, contextual)
        if not contextual:
            _assert_builder_call_legacy_operand_ir(tmp_path, monkeypatch)
            _assert_builder_call_provider_scalar_contract(monkeypatch)


def _assert_scaffold_object_result_is_published_at_return(
    tmp_path, monkeypatch, expression, suffix, contextual, caller_sink=True,
    *, zero_arguments=False,
):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
    provider = Path(__file__).resolve().parents[2] / "pcc/ir/ir.py"
    source = tmp_path / "probe.py"
    returned_expression = "take(value=" + expression + ")" if caller_sink else expression
    source.write_text("from pcc.ir.compat import ir\n"
                      "def take(*, value, **extras):\n    return value\n"
                      "def probe(operand):\n    builder = ir.IRBuilder()\n"
                      "    return " + returned_expression + "\n")
    paths = [str(provider), str(source)] if contextual else [str(source)]
    names = ["pcc.ir.ir", "probe"] if contextual else ["probe"]
    modules, exports, derived = build_closed_world_context(paths, names)
    typed = infer_module(modules[-1], external_exports=exports, derived_class_map=derived)
    if suffix == "scaffold_Constant_none" and not contextual:
        from pcc.frontends.python.py_ast import (
            DynType,
            FuncDef,
            ListExpr,
            Return,
        )

        probe = next(node for node in typed.body if isinstance(node, FuncDef) and node.name == "probe")
        returned = next(node for node in probe.body if isinstance(node, Return))
        constant = returned.value.kwargs[0][1] if caller_sink else returned.value
        if isinstance(constant, ListExpr):
            constant = constant.elems[0]
        # No fabricated ownership/type flags: real inference leaves this
        # no-provider call Dyn, so only its actual producer can own the result.
        assert isinstance(constant.ty, DynType)
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
    codegen._native_module_exports = exports if contextual else None
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    text = str(codegen.generate(typed))
    (tmp_path / "probe.ll").write_text(text)
    body = re.search(r"^define [^\n]*@user_probe_probe\([^\n]*\).*?^}", text, re.M | re.S).group(0)
    calls = list(re.finditer(r"(%[^\s]+) = call ptr [^\n]*@user_pcc_ir_ir_" + suffix + r"\([^\n]*\)\n", body))
    assert len(calls) == 1
    assert body[calls[0].end():].lstrip().startswith("store ptr " + calls[0].group(1) + ", ptr ")
    assert not re.search(r"\bcall [^\n]*@py_cpy_", body)
    # Publication's fallible lease checks run before the callee-error check.
    # Their error target must include argument unpins, not only output roots.
    publication = body[calls[0].end():]
    cleanup_target = re.search(r"label %(scaffold\.arguments\.cleanup[^,\s]+)", publication)
    assert cleanup_target is not None
    cleanup = re.search(r"^" + re.escape(cleanup_target.group(1)) + r":\n(.*?)(?=^\S|\Z)", body, re.M | re.S)
    assert cleanup is not None
    # The error edge undoes exactly the argument pins the success edge undoes.
    # Static literals are never pinned; with the provider in view the managed
    # receiver is, so that case must unpin something.
    continuation = re.search(
        r"br i1 %err\.cmp[^,\n]*, label %" + re.escape(cleanup_target.group(1)) + r", label %([^\s,]+)\n",
        publication,
    )
    assert continuation is not None
    success = re.search(r"^" + re.escape(continuation.group(1)) + r":\n(.*?)(?=^\S|\Z)", body, re.M | re.S)
    assert success is not None
    cleanup_unpins = re.findall(r"@pcc_gc_unpin\(ptr ([^)]+)\)", cleanup.group(1))
    success_unpins = re.findall(r"@pcc_gc_unpin\(ptr ([^)]+)\)", success.group(1))
    assert sorted(cleanup_unpins) == sorted(success_unpins)
    if zero_arguments:
        assert not cleanup_unpins
    else:
        assert not contextual or cleanup_unpins


@pytest.mark.parametrize("contextual", [False, True])
@pytest.mark.parametrize("expression,caller_sink", [
    ("ir.Context()", True), ("[ir.Context()]", True), ("ir.Context()", False),
])
def test_context_factory_publishes_its_fresh_owner_without_argument_pins(
    tmp_path, monkeypatch, contextual, expression, caller_sink,
):
    _assert_scaffold_object_result_is_published_at_return(
        tmp_path, monkeypatch, expression, "scaffold_Context", contextual,
        caller_sink=caller_sink, zero_arguments=True,
    )


@pytest.mark.parametrize("expression,suffix", [
    ("builder.store(operand, operand)", "IRBuilder_store"),
    ("builder.ret(operand)", "IRBuilder_ret"),
    ("builder.ret_void()", "IRBuilder_ret_void"),
    ("builder.branch(operand)", "IRBuilder_branch"),
    ("builder.cbranch(operand, operand, operand)", "IRBuilder_cbranch"),
    ("builder.unreachable()", "IRBuilder_unreachable"),
    ("builder.fence('seq_cst')", "IRBuilder_fence"),
    ("builder.store_atomic(operand, operand, 'release', 4)", "IRBuilder_store_atomic"),
])
def test_void_ir_operations_keep_provider_value_result(tmp_path, monkeypatch, expression, suffix):
    # An emitted LLVM void operation still has a managed Python Value result.
    test_scaffold_object_result_is_published_at_return(
        tmp_path, monkeypatch, expression, suffix, contextual=True,
    )


@pytest.mark.parametrize("manual", [False, True])
def test_scaffold_rejects_provider_raw_or_manual_pointer_result(tmp_path, monkeypatch, manual):
    for symbol, expression in (
        ("scaffold_IntType", "ir.IntType(64)"),
        ("scaffold_Constant_none", "ir.Constant(operand, None)"),
        ("scaffold_Constant_obj", "ir.Constant(operand, operand)"),
        ("scaffold_Context", "ir.Context()"),
    ):
        _assert_scaffold_rejects_provider_raw_or_manual_pointer_result(
            tmp_path, monkeypatch, manual, symbol, expression,
        )
    _assert_builder_call_excluded_results(tmp_path, monkeypatch, manual)


def _assert_scaffold_rejects_provider_raw_or_manual_pointer_result(
    tmp_path, monkeypatch, manual, symbol, expression,
):
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
    provider = tmp_path / "provider.py"
    parameter = ("" if symbol == "scaffold_Context" else
                 "width: int" if symbol == "scaffold_IntType" else
                 "ty, value" if symbol == "scaffold_Constant_obj" else "ty")
    raw_value = "width" if symbol == "scaffold_IntType" else "0"
    provider.write_text("from pcc.extern import c_ptr\nfrom pcc.unsafe import int_to_ptr\n"
                        + ("__pcc_runtime_port__ = True\n" if manual else "")
                        + "def " + symbol + "(" + parameter + ")" + ("" if manual else " -> c_ptr")
                        + ":\n    return int_to_ptr(" + raw_value + ")\n")
    entry = tmp_path / "probe.py"
    entry.write_text("from pcc.ir.compat import ir\n"
                    "def take(*, value, **extras):\n    return value\n"
                    "def probe(operand):\n    return take(value=" + expression + ")\n")
    modules, exports, derived = build_closed_world_context(
        [str(provider), str(entry)], ["pcc.ir.ir", "probe"])
    info = exports["pcc.ir.ir"][symbol]
    assert info["return_ty"] == (("dyn",) if manual else ("raw_pointer",))
    assert info["manual_pointer_abi"] is manual
    typed = infer_module(modules[1], external_exports=exports, derived_class_map=derived)
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
    codegen._native_module_exports = exports
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    with pytest.raises(L1CodegenError, match="scaffold call has no managed result contract"):
        codegen.generate(typed)
    text = str(codegen.module)
    assert not re.search(r"\bcall [^\n]*@user_pcc_ir_ir_" + symbol + r"\(", text)


def test_unknown_pointer_declaration_does_not_imply_owned_result():
    from types import SimpleNamespace
    from pcc.ir.compat import ir
    from pcc.frontends.python.codegen.ir_scaffold_lowering import _scaffold_emit_declared_call

    module = ir.Module("unknown_pointer")
    function = ir.Function(module, ir.FunctionType(ir.IntType(8).as_pointer(), []), "foreign_pointer")
    host = SimpleNamespace(module=module, _native_module_exports=None,
                           _manual_pointer_abi_functions=set(),
                           _slot_call_result_sink=lambda expr: object())
    # Rejection precedes operand evaluation or any attempt to use a builder.
    with pytest.raises(L1CodegenError, match="no managed result contract"):
        _scaffold_emit_declared_call(host, function.name, function.function_type.return_type,
                                     (), call_expr=object())
    _assert_builder_call_finish_contract()
    _assert_builder_call_finish_contract("scaffold_Constant_obj")
    _assert_builder_call_legacy_handles()


def _assert_builder_call_result_sinks(tmp_path, monkeypatch, contextual):
    cases = [
        ("arity" + str(arity), "builder.call(operand, ["
         + ", ".join(["operand"] * arity) + "])", "IRBuilder_call" + str(arity))
        for arity in range(10)
    ]
    cases.extend([
        ("tuple", "builder.call(operand, (operand, operand), name='tuple')", "IRBuilder_call2"),
        ("dynamic", "builder.call(operand, arguments, name='dynamic')", "IRBuilder_call_dyn"),
        ("positional", "builder.call(operand, [operand], 'positional')", "IRBuilder_call1"),
    ])
    source_text = "from pcc.ir.compat import ir\n"
    source_text += "def take(*, value, **extras):\n    return value\n"
    for name, expression, _suffix in cases:
        source_text += "def probe_" + name + "(operand, arguments):\n"
        source_text += "    builder = ir.IRBuilder()\n    return take(value=" + expression + ")\n"
    source_text += (
        "def probe_lambda(operand):\n    builder = ir.IRBuilder()\n"
        "    return lambda index, label: builder.call(operand, "
        "[operand, ir.Constant(ir.IntType(64), index)], name=label)\n"
    )
    source = tmp_path / "builder_results.py"
    source.write_text(source_text)
    provider = Path(__file__).resolve().parents[2] / "pcc/ir/ir.py"
    paths = [str(provider), str(source)] if contextual else [str(source)]
    names = ["pcc.ir.ir", "builder_results"] if contextual else ["builder_results"]
    modules, exports, derived = build_closed_world_context(paths, names)
    typed = infer_module(modules[-1], external_exports=exports, derived_class_map=derived)
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
    codegen._native_module_exports = exports if contextual else None
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    text = str(codegen.generate(typed))
    (tmp_path / "builder_results.ll").write_text(text)
    for name, _expression, suffix in cases:
        body = re.search(r"^define [^\n]*@user_builder_results_probe_" + name
                         + r"\([^\n]*\).*?^}", text, re.M | re.S).group(0)
        _assert_builder_sink_publication_and_cleanup(body, suffix)
    bodies = re.findall(r"^define [^\n]*@user_builder_results_[^\n]*\([^\n]*\).*?^}", text, re.M | re.S)
    lambdas = [body for body in bodies if "__native_lambda_" in body.splitlines()[0]
               and re.search(r"\bcall [^\n]*@user_pcc_ir_ir_IRBuilder_call2\(", body)]
    assert len(lambdas) == 1
    _assert_builder_sink_publication_and_cleanup(lambdas[0], "IRBuilder_call2")


def _pending_exception_edge(publication):
    # Bind the first pending-exception probe, its comparison and branch. A
    # later, unrelated call must not satisfy the current provider's contract.
    probe = publication.find("@py_err_occurred(")
    assert probe >= 0
    start = publication.rfind("\n", 0, probe) + 1
    edge = re.match(
        r"  (?P<flag>%[-.\w]+) = call i64(?: \(\))? @py_err_occurred\(\)\n"
        r"  (?P<cmp>%[-.\w]+) = icmp ne i64 (?P=flag), 0\n"
        r"  br i1 (?P=cmp), label %(?P<error>[^,\s]+), label %(?P<success>[^,\s]+)\n",
        publication[start:],
    )
    assert edge is not None
    return start, edge["error"], edge["success"]


@pytest.mark.parametrize("signature", ["", " ()"])
def test_pending_exception_edge_requires_first_probe_ssa(signature):
    edge = (f"  %flag.1 = call i64{signature} @py_err_occurred()\n"
            "  %cmp.1 = icmp ne i64 %flag.1, 0\n"
            "  br i1 %cmp.1, label %failure, label %resume\n")
    assert _pending_exception_edge(edge) == (0, "failure", "resume")
    for original, wrong in (("i64 %flag.1,", "i64 %other,"),
                            ("br i1 %cmp.1,", "br i1 %other,"),
                            ("icmp ne", "icmp eq")):
        with pytest.raises(AssertionError):
            _pending_exception_edge(edge.replace(original, wrong) + edge)


def _assert_builder_sink_publication_and_cleanup(body, suffix):
    calls = list(re.finditer(r"(%[^\s]+) = call ptr [^\n]*@user_pcc_ir_ir_" + suffix + r"\([^\n]*\)\n", body))
    assert len(calls) == 1
    publication = body[calls[0].end():]
    store = re.match(r"\s*store ptr " + re.escape(calls[0][1]) + r", ptr (%[^\s,]+)", publication)
    assert store is not None
    slot = store[1]
    check_start, error, success = _pending_exception_edge(publication)
    assert "@pcc_gc_foreign_lease_release" in publication[:check_start]
    blocks = dict(re.findall(r"^([^\s:]+):\n(.*?)(?=^[^\s:]+:\n|^}|\Z)", body, re.M | re.S))
    assert re.search(r"%scaffold\.current[^\s]* = load ptr, ptr " + re.escape(slot) + r"(?:,|$)",
                     blocks[success], re.M)
    # Publication status failures and the provider's pending native exception
    # must retire this existing caller output root exactly once. No argument
    # pinning/release protocol is added by this result-only change.
    # The inline no-op lease gates (pcc_gc_leases_inert set, a 0 token at
    # release) branch to their join, not to a failure edge.
    failure_targets = [
        target for condition, target in re.findall(
            r"br i1 ([^,\n]+), label %([^,\s]+), label %[^,\s]+", publication[:check_start])
        if ".lease.inert.set." not in condition and ".lease.none." not in condition
    ]
    failure_targets.append(error)
    for target in failure_targets:
        pending = [(target, (), 0)]
        counts = []
        while pending:
            label, seen, count = pending.pop()
            assert label not in seen
            block = blocks[label]
            count += len(re.findall(
                r"@py_cleanup_(?:one_root|two_roots)_preserving_exception\([^\n)]*ptr "
                + re.escape(slot) + r"(?:,|\))", block,
            ))
            labels = re.findall(r"\blabel %([^,\s]+)", block)
            if labels:
                for successor in labels:
                    pending.append((successor, seen + (label,), count))
            else:
                counts.append(count)
        assert counts and set(counts) == {1}, (target, counts)


def _assert_builder_call_legacy_operand_ir(tmp_path, monkeypatch):
    from pcc.frontends.python.codegen import ir_scaffold_lowering as lowering
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.pipeline import (
        PyPipelineError,
        _reject_cpython_bridge_calls,
        compile_python,
    )

    original_finish = lowering._scaffold_call_with_result_owner

    def emit(source, module_name, *, legacy_raw=False):
        typed = infer_module(parse_and_lift(source, module_name + ".py", module_name))
        codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
        codegen._strict_no_libpython = False
        snapshots = []

        def finish(host, fn, args, name, call_expr):
            assert host._slot_call_result_sink(call_expr) is None
            # Freeze the emitted operand/declaration state now. Later return
            # lowering can hoist allocas into entry and legitimately differ
            # because the new producer metadata avoids a borrowed retain.
            snapshots.append((
                tuple((block.name, tuple(str(record) for record in block._instrs))
                      for block in host.current_function.blocks),
                fn.name, str(fn.function_type), tuple(str(value) for value in args), name,
                tuple(sorted(str(value) for value in host._cpy_values)),
                tuple(sorted(str(value) for value in host._owned_cpy_values)),
            ))
            if legacy_raw:
                return host.builder.call(fn, args, name=name)
            return original_finish(host, fn, args, name, call_expr)

        with monkeypatch.context() as instrument:
            instrument.setattr(lowering, "_scaffold_call_with_result_owner", finish)
            text = str(codegen.generate(typed))
        body = re.search(r"^define [^\n]*@user_" + module_name + r"_probe\([^\n]*\).*?^}",
                         text, re.M | re.S).group(0)
        assert len(snapshots) == 1
        return text, body, snapshots, sorted(str(value) for value in codegen._cpy_values), sorted(
            str(value) for value in codegen._owned_cpy_values)

    expressions = [
        ("legacy_literal", "", "builder.call(operand, [41, floating, truth, None, 1208925819614629174706176], name='named')", "IRBuilder_call5"),
        ("legacy_dynamic", "", "builder.call(operand, arguments)", "IRBuilder_call_dyn"),
        ("legacy_foreign", "from decimal import Decimal\n", "builder.call(operand, [Decimal('2')], name=Decimal('1').to_eng_string())", "IRBuilder_call1"),
    ]
    for module_name, imports, expression, suffix in expressions:
        source = ("from pcc.ir.compat import ir\n" + imports
                  + "def probe(operand, arguments, floating: float, truth: bool):\n"
                  + "    builder = ir.IRBuilder()\n    return " + expression + "\n")
        actual = emit(source, module_name)
        # With the finisher replaced by the old raw call, the unmodified
        # emitter is the original argument/declaration/call protocol.
        expected = emit(source, module_name, legacy_raw=True)
        call_pattern = r"(%[^\s]+) = call ptr [^\n]*@user_pcc_ir_ir_" + suffix + r"\([^\n]*\)\n"
        new_call = re.search(call_pattern, actual[1])
        old_call = re.search(call_pattern, expected[1])
        assert new_call is not None and old_call is not None
        assert new_call[0] == old_call[0]
        assert actual[2:] == expected[2:]
        assert "scaffold.current" not in actual[1]
        assert "scaffold.result" not in actual[1]
        for symbol in ("py_cpy_to_pcc_obj", "py_cpy_decref"):
            assert actual[1].count("@" + symbol + "(") == expected[1].count("@" + symbol + "(")
        if module_name == "legacy_foreign":
            # Artifact-only generation remains allowed; native admission is
            # still the existing explicit unsupported frontier, not a new gate.
            with pytest.raises(PyPipelineError, match=r"generated IR still calls py_cpy_\*"):
                _reject_cpython_bridge_calls(actual[0], "builder.call foreign input")
        (tmp_path / (module_name + ".ll")).write_text(actual[0])
    public_source = tmp_path / "foreign_unavailable.py"
    public_output = tmp_path / "foreign_unavailable.ll"
    public_source.write_text(
        "from pcc.ir.compat import ir\n"
        "def probe(operand):\n"
        "    from unavailable_compatibility_provider import make\n"
        "    builder = ir.IRBuilder()\n"
        "    return builder.call(operand, [], name=make())\n"
    )
    compile_python(str(public_source), str(public_output), emit_llvm_only=True,
                   ir_scaffold_mode="on")
    text = public_output.read_text()
    message = "No module named 'unavailable_compatibility_provider'"
    assert "".join("\\" + format(byte, "02X") for byte in message.encode()) in text
    assert "@py_raise" in text
    assert not re.search(r"\bcall [^\n]*@py_cpy_", text)


def _assert_builder_call_excluded_results(tmp_path, monkeypatch, manual):
    for symbol, expression, parameters in (
        ("IRBuilder_call0", "builder.call(operand, [])", "builder, fn, name"),
        ("IRBuilder_call_dyn", "builder.call(operand, arguments)", "builder, fn, arguments, name"),
    ):
        provider = tmp_path / "builder_provider.py"
        provider.write_text("from pcc.extern import c_ptr\nfrom pcc.unsafe import int_to_ptr\n"
                            + ("__pcc_runtime_port__ = True\n" if manual else "")
                            + "def " + symbol + "(" + parameters + ")" + ("" if manual else " -> c_ptr")
                            + ":\n    return int_to_ptr(0)\n")
        entry = tmp_path / "builder_probe.py"
        entry.write_text("from pcc.ir.compat import ir\n"
                         "def take(*, value, **extras):\n    return value\n"
                         "def probe(operand, arguments):\n    builder = ir.IRBuilder()\n"
                         "    return take(value=" + expression + ")\n")
        modules, exports, derived = build_closed_world_context(
            [str(provider), str(entry)], ["pcc.ir.ir", "builder_probe"])
        info = exports["pcc.ir.ir"][symbol]
        assert info["return_ty"] == (("dyn",) if manual else ("raw_pointer",))
        assert info["manual_pointer_abi"] is manual
        typed = infer_module(modules[1], external_exports=exports, derived_class_map=derived)
        codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
        codegen._native_module_exports = exports
        codegen._strict_no_libpython = True
        with pytest.raises(L1CodegenError, match="scaffold call has no managed result contract"):
            codegen.generate(typed)
        assert not re.search(r"\bcall [^\n]*@user_pcc_ir_ir_" + symbol + r"\(", str(codegen.module))


def _assert_builder_call_finish_contract(symbol="IRBuilder_call0"):
    from types import SimpleNamespace
    from pcc.ir.compat import ir
    from pcc.frontends.python.codegen.ir_scaffold_lowering import _scaffold_call_with_result_owner
    from pcc.frontends.python.export_meta import encode_type
    from pcc.frontends.python.py_ast import ClassType

    pointer = ir.IntType(8).as_pointer()
    payload = ClassType(name="Payload", module="provider", valueclass=True)
    for actual_return, info, manual, freestanding, expected_owner in (
        (pointer, None, False, False, True),
        (pointer, {"return_ty": ("raw_pointer",)}, False, False, False),
        (pointer, {"manual_pointer_abi": True}, False, False, False),
        (pointer, None, True, False, False),
        (pointer, None, False, True, False),
        (pointer, {"return_ty": encode_type(payload)}, False, False, False),
        (ir.IntType(64), None, False, False, False),
        (ir.VoidType(), None, False, False, False),
    ):
        for sink in (None, object()):
            events = []
            module = ir.Module("finish_contract")
            fn = ir.Function(module, ir.FunctionType(actual_return, [pointer]), "user_pcc_ir_ir_" + symbol)
            result = ir.Value(actual_return, "%result")
            current = ir.Value(actual_return, "%current")
            args = [object()]
            span = object()
            expression = SimpleNamespace(span=span)
            host = SimpleNamespace(
                _native_module_exports={"pcc.ir.ir": {symbol: info}} if info else None,
                _manual_pointer_abi_functions={fn.name} if manual else set(),
                _freestanding_module=freestanding,
                _is_valueclass_payload_type=lambda ty: isinstance(ty, ClassType) and ty.valueclass,
                _slot_call_result_sink=lambda expr: sink,
                _note_owned_object_value=lambda value: events.append(("owner", value)),
                _publish_slot_call_owned=lambda slot, value, **kwargs: events.append(("publish", slot, value)),
                _emit_post_call_err_check=lambda source: events.append(("error", source)),
                _fresh=lambda label: label,
                _cpy_values={args[0]}, _owned_cpy_values={args[0]},
            )
            host.builder = SimpleNamespace(
                call=lambda function, values, name: (events.append(("call", function, values, name)), result)[1],
                load=lambda slot, name: (events.append(("load", slot)), current)[1],
            )
            if sink is not None and isinstance(actual_return, ir.PointerType) and not expected_owner:
                with pytest.raises(L1CodegenError, match="no managed result contract"):
                    _scaffold_call_with_result_owner(host, fn, args, "unchanged", expression)
                assert events == []
                continue
            value = _scaffold_call_with_result_owner(host, fn, args, "unchanged", expression)
            assert events[0] == ("call", fn, args, "unchanged")
            if expected_owner and sink is not None:
                assert value is current
                assert events[1:] == [("publish", sink, result), ("error", span), ("load", sink)]
            else:
                assert value is result
                assert events[1:] == ([("owner", result)] if expected_owner else [])
            assert host._cpy_values == host._owned_cpy_values == {args[0]}


def _assert_builder_call_legacy_handles():
    from types import SimpleNamespace
    from pcc.ir.compat import ir
    from pcc.frontends.python.codegen.ir_scaffold_lowering import IrScaffoldLoweringMixin
    from pcc.frontends.python.py_ast import BoolType, DynType, FloatType, IntType

    module = ir.Module("legacy_handles")
    pointer = ir.IntType(8).as_pointer()
    fn = ir.Function(module, ir.FunctionType(pointer, [pointer]), "probe")
    builder = ir.IRBuilder(fn.append_basic_block("entry"))
    builder._direct_indexed_capture = False
    builder._direct_indexed_no_text = False
    bool_box = ir.Function(module, ir.FunctionType(pointer, [ir.IntType(32)]), "py_bool_from_bit")
    host = SimpleNamespace(builder=builder, runtime={"py_bool_from_bit": bool_box},
                           _emit_expr=lambda expr: expr.raw, _fresh=lambda hint: hint,
                           _cpy_values={fn.args[0]}, _owned_cpy_values={fn.args[0]})
    emit = IrScaffoldLoweringMixin._scaffold_to_handle
    integer = emit(host, SimpleNamespace(raw=ir.Constant(ir.IntType(64), 41), ty=IntType(name="int")))
    floating = emit(host, SimpleNamespace(raw=ir.Constant(ir.DoubleType(), 2.5), ty=FloatType(name="float")))
    boolean = emit(host, SimpleNamespace(raw=ir.Constant(ir.IntType(1), 1), ty=BoolType(name="bool")))
    # Pointer-form integers and CPython-domain values stay pointer pass-through.
    # The caller position (receiver, target, element or name) adds no bridge.
    for _position in ("receiver", "target", "element", "name"):
        assert emit(host, SimpleNamespace(raw=fn.args[0], ty=DynType(name="dyn"))) is fn.args[0]
    assert emit(host, SimpleNamespace(raw=fn.args[0], ty=IntType(name="int"))) is fn.args[0]
    text = str(module)
    assert re.search(r"inttoptr i64 41 to ptr", text)
    assert re.search(r"bitcast double [^\n]+ to i64", text)
    assert "@py_bool_from_bit(" in text
    assert "@py_float_from_f64(" not in text and "@py_int_from_i64(" not in text
    assert "@py_cpy_to_pcc_obj(" not in text and "@py_cpy_decref(" not in text
    assert all(isinstance(value.type, ir.PointerType) for value in (integer, floating, boolean))
    assert host._cpy_values == host._owned_cpy_values == {fn.args[0]}


def _assert_builder_call_provider_scalar_contract(monkeypatch):
    from pcc.ir import ir as provider

    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    word = provider.IntType(128)

    def setup(count, *, var_arg=False, argument_type=None):
        module = provider.Module(name="provider_scalar_contract")
        target = provider.Function(
            module, provider.FunctionType(
                word, [word if argument_type is None else argument_type] * count,
                var_arg=var_arg,
            ),
            name="target",
        )
        caller = provider.Function(module, provider.FunctionType(word, []), name="caller")
        builder = provider.IRBuilder(caller.append_basic_block("entry"))
        return module, target, builder

    events = []

    class Renderable:
        def __str__(self):
            events.append("render")
            return "37"

    class DuckFunction:
        def __init__(self, function):
            self.name = function.name
            self.ftype = function.ftype

    # Acceptance here means the provider renders these objects. It does not
    # claim that e.g. True or None is valid LLVM operand syntax. Integer and
    # float cases also use a suitable represented argument type.
    values = [41, 1208925819614629174706176, 2.5, True, None, Renderable()]
    expected = ["41", "1208925819614629174706176", "2.5", "True", "None", "37"]
    types = [word, word, provider.DoubleType(), provider.IntType(1), word.as_pointer(), word]
    for value, rendered, argument_type in zip(values, expected, types):
        module, target, builder = setup(1, argument_type=argument_type)
        for fn in (target, DuckFunction(target), provider.Value(target.ftype.as_pointer(), "%indirect")):
            result = provider.IRBuilder_call1(builder, fn, value, "scalar")
            assert str(result.type) == "i128"
            assert str(argument_type) + " " + rendered in str(module)
        assert str(provider.IRBuilder_call_dyn(builder, target, [value], "dynamic").type) == "i128"
    assert events == ["render"] * 4
    # Exercise every fixed shim, not only call1, with a legal integer object.
    for arity in range(2, 10):
        module, target, builder = setup(arity)
        result = getattr(provider, "IRBuilder_call" + str(arity))(
            builder, target, *([41] * arity), name="fixed",
        )
        assert str(result.type) == "i128"
        assert str(module).count("i128 41") == arity
    # Existing rejection boundaries are deliberately not repaired or hidden:
    # extra arguments need .type, and str.index is not an operand index.
    for value in (41, 2.5, True, None):
        _module, target, builder = setup(0, var_arg=True)
        with pytest.raises(AttributeError):
            provider.IRBuilder_call1(builder, target, value)
    _module, target, builder = setup(1)
    with pytest.raises(TypeError):
        provider.IRBuilder_call1(builder, target, "41")
    # Direct records have a separate existing Value-shaped contract: even a
    # fixed signature still reads operand._ref. Both capture+text and no-text
    # routes retain that rejection rather than wrapping a scalar as Constant.
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_VALIDATE", "0")
    monkeypatch.setenv("PCC_TEXT_INDEXED_KERNEL_EMIT", "0")
    for emit in ("0", "1"):
        monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", emit)
        for arity in (1, 2, 3):
            _module, target, builder = setup(arity)
            assert builder._direct_indexed_capture
            assert builder._direct_indexed_no_text == (emit == "1")
            with pytest.raises(AttributeError):
                getattr(provider, "IRBuilder_call" + str(arity))(
                    builder, target, *([41] * arity),
                )
        _module, target, builder = setup(1)
        with pytest.raises(AttributeError):
            provider.IRBuilder_call_dyn(builder, target, [41])
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")



def _assert_constant_obj_result_sinks(tmp_path, monkeypatch, contextual):
    from pcc.frontends.python.codegen import ir_scaffold_lowering as lowering

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
    source = tmp_path / "constant_obj_results.py"
    source.write_text(
        "from pcc.ir.compat import ir\n"
        "def take(*, value, **extras):\n    return value\n"
        "def probe_direct(kind, operand):\n    return take(value=ir.Constant(kind, operand))\n"
        "def probe_tuple(kind, operand):\n    return take(value=(ir.Constant(kind, operand), operand))\n"
        "def probe_lambda(kind):\n    return lambda operand: ir.Constant(kind, operand)\n"
        "def probe_nested(value):\n    return lambda: ir.Constant(ir.IntType(64), value)\n"
        "def probe_standalone(kind, operand):\n    return ir.Constant(kind, operand)\n"
    )
    provider = Path(__file__).resolve().parents[2] / "pcc/ir/ir.py"
    paths = [str(provider), str(source)] if contextual else [str(source)]
    names = ["pcc.ir.ir", "constant_obj_results"] if contextual else ["constant_obj_results"]
    modules, exports, derived = build_closed_world_context(paths, names)
    typed = infer_module(modules[-1], external_exports=exports, derived_class_map=derived)
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
    codegen._native_module_exports = exports if contextual else None
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    original = lowering._scaffold_call_with_result_owner
    standalone_events = []

    def checked_finish(host, fn, args, name, call_expr, **kwargs):
        if fn.name != "user_pcc_ir_ir_scaffold_Constant_obj":
            return original(host, fn, args, name, call_expr, **kwargs)
        assert len(args) == 2
        assert len(fn.args) == 2
        if host._slot_call_result_sink(call_expr) is not None:
            return original(host, fn, args, name, call_expr, **kwargs)
        # The actual no-sink producer must still emit exactly the original
        # call: no root, conversion, pin, release, or exception branch.
        block = host.builder._block
        before = len(block._instrs)
        result = original(host, fn, args, name, call_expr, **kwargs)
        added = block._instrs[before:]
        assert host.builder._block is block
        assert len(added) == 1
        assert "@user_pcc_ir_ir_scaffold_Constant_obj(" in str(added[0])
        standalone_events.append(True)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(lowering, "_scaffold_call_with_result_owner", checked_finish)
        text = str(codegen.generate(typed))
    bodies = re.findall(r"^define [^\n]*@user_constant_obj_results_[^\n]*\([^\n]*\).*?^}", text, re.M | re.S)
    result_bodies = [body for body in bodies
                     if re.search(r"\bcall [^\n]*@user_pcc_ir_ir_scaffold_Constant_obj\(", body)]
    assert len(result_bodies) == 5
    for body in result_bodies:
        if "probe_standalone(" not in body.splitlines()[0]:
            _assert_builder_sink_publication_and_cleanup(body, "scaffold_Constant_obj")
    assert standalone_events == [True]
    assert not re.search(r"\bcall [^\n]*@py_cpy_", text)
    (tmp_path / "constant_obj_results.ll").write_text(text)
