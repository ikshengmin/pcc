# Relocation attribute lowering: rejected counter change

The real `OwnedMergedSourceView` count loop is in
`native_object._final_section_rows`; the existing projection loop is
`macho_obj._append_final_link_relocation_fields`. Moving the former loop
beside `Relocation` and casting exact records **did not remove its two
conditional attribute protocol calls**. The proposed exact-type guard also
added a `py_type_builtin` site. Do not land that production change.

Reproduce against a supplied checkout:

```sh
uv run python scripts/research/relocation_attribute_lowering.py --repo .
uv run python -m pytest -x tests/python/test_relocation_attribute_lowering.py
```

The script uses four real modules through PCC's existing closed-world
context builder, full-module inference, fresh-instance frontend emission,
and owned parser/verifier. It emits only the selected functions and needed
local class declaration, not the complete Stage1 program. A stub or
`py_cpy_` call in the inspected function fails admission. The proposal is
added in memory; production files are not edited. `--ir-output` optionally
writes local evidence, not repository fixtures.

At `b20da703`, baseline and proposed exact branch each retained one
`py_obj_getattr` for `addend` and one for `type`. The existing append helper
also retained those reads, while 17 other projection sites used
`py_instance_get_field`. Therefore its historical optimization is not
uniformly ineffective: conditional/slot-call reads and indexed projections
are distinct paths. Current ordinary-class lowering keeps live namespace
semantics; descriptors, rebinding and subclasses cannot be bypassed merely
because a record is frozen or slotted. The runtime already has epoch-checked
attribute resolution and no-`__getattribute__` caches; a getattr site does
not imply a repeated full MRO scan.

Evidence is host-local IR, not native timing, a complete export-graph proof,
or five-GC qualification. Initial extraction without local exports produced
strict stubs and was rejected, not counted as a speedup. Borrowing a stable
receiver is a separate mechanism that can preserve live lookup; this experiment
does not enable either production change.
