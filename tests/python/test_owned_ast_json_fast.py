"""Private AST JSON reference subset: ownership and fallback, not public JSON."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


class _Heap:
    """Counted-object, checked-root and raw-byte model for runtime source."""

    def __init__(self, module, monkeypatch, text, fail_at=0):
        self.next = 4096
        self.raw = {}
        self.allocations = set()
        self.words = {}
        self.objects = {}
        self.refs = {}
        self.immortal = {}
        self.roots = set()
        self.pending = None
        self.cleanup_raises = False
        self.sheltered = False
        self.attempt = 0
        self.fail_at = fail_at
        self.dict_sizes = []
        self.cached = []
        self.input = self.obj(text)
        self.constants = {name: self.static(value) for name, value in
                          [("py_None", None), ("py_True", True), ("py_False", False)]}
        ops = {
            "null": lambda: 0, "ptr_is_null": lambda p: int(p == 0),
            "ptr_add": lambda p, n: p+n, "ptr_eq": lambda a,b: int(a==b),
            "stack_alloc": self.alloc, "malloc": self.try_alloc,
            "realloc": self.realloc, "free": self.free,
            "memcpy": self.memcpy, "memset": self.memset,
            "load_i8": lambda p,i: self.raw.get(p+i,0),
            "store_i8": lambda p,i,v: self.raw.__setitem__(p+i,v & 255),
            "load_i64": self.load, "load_ptr": self.load,
            "store_i64": self.store, "store_ptr": self.store, "store_i32": self.store,
            "cstr": self.cstr, "_type_of": lambda p: 4 if isinstance(self.objects[p],str) else -1,
            "py_str_utf8": lambda p: self.cstr(self.objects[p]),
            "py_str_byte_len": lambda p: len(self.objects[p].encode("utf-8")),
            "py_str_new": self.new_string, "py_int_from_i64": self.new_integer,
            "pcc_str_ascii_char": self.ascii_char,
            "py_dict_new_presized": self.new_dict,
            "py_list_new": lambda capacity: self.new([]),
            "py_dict_set_slots": self.set_slots,
            "py_list_append": self.append,
            "py_list_append_from_slots": self.append_slots,
            "py_list_len": lambda p: len(self.objects[p]),
            "pcc_gc_register_continuation_root_node": self.register,
            "pcc_gc_unregister_continuation_root_node": self.unregister,
            "pcc_gc_store_root_take": self.take,
            "pcc_gc_retain": self.retain, "py_decref": self.release,
            "global_load_ptr": lambda name: self.constants[name],
            "py_err_occurred": lambda: int(self.pending is not None),
            "py_tls_exc_swap_slot": self.swap_exc,
            "py_clear_exception": lambda: setattr(self, "pending", None),
            "py_exc_new": lambda kind,msg: kind, "py_raise_owned": self.raise_error,
        }
        for key, value in ops.items():
            monkeypatch.setattr(module, key, value, raising=False)

    def alloc(self, size):
        p = self.next
        self.next += size + 16
        return p

    def fails(self):
        self.attempt += 1
        return self.attempt == self.fail_at

    def try_alloc(self, size):
        if self.fails():
            return 0
        p = self.alloc(size)
        self.allocations.add(p)
        return p

    def realloc(self, p, size):
        new = self.try_alloc(size)
        if new and p:
            # The model retains old bytes for the copied live prefix.
            for offset in range(size):
                self.raw[new+offset] = self.raw.get(p+offset, 0)
            self.free(p)
        return new

    def free(self, p):
        self.allocations.remove(p)

    def memcpy(self, dst, src, n):
        for i in range(n):
            self.raw[dst+i] = self.raw.get(src+i, 0)

    def memset(self, dst, value, n):
        for i in range(n):
            self.raw[dst+i] = value
            self.words[dst+i] = value

    def cstr(self, text):
        b = text.encode("utf-8") + b"\0"
        p = self.alloc(len(b))
        for i, v in enumerate(b):
            self.raw[p+i] = v
        return p

    def load(self, p, offset=0):
        return self.words.get(p+offset, 0)

    def store(self, p, offset, value):
        self.words[p+offset] = value

    def obj(self, value):
        p = self.alloc(64)
        self.objects[p] = value
        self.refs[p] = 1
        return p

    def static(self, value):
        key = (type(value), value)
        if key not in self.immortal:
            self.immortal[key] = self.obj(value)
        return self.immortal[key]

    def new(self, value):
        return 0 if self.fails() else self.obj(value)

    def new_string(self, p, n):
        return self.new(bytes(self.raw.get(p+i,0) for i in range(n)).decode("utf-8"))

    def new_integer(self, value):
        return self.new(value)

    def ascii_char(self, ch):
        self.cached.append(ch)
        if self.fails():
            return 0  # The production cache has an allocation fallback.
        return self.static(chr(ch))

    def new_dict(self, expected):
        self.dict_sizes.append(expected)
        return self.new({})

    def retain(self, p):
        if p and p not in self.immortal.values():
            self.refs[p] += 1
        return p

    def release(self, p):
        if not p or p in self.immortal.values():
            return
        self.refs[p] -= 1
        assert self.refs[p] >= 0
        if not self.refs[p]:
            value = self.objects.pop(p)
            del self.refs[p]
            if isinstance(value, list):
                for child in value:
                    self.release(child)
            elif isinstance(value, dict):
                for key, child in value.values():
                    self.release(key)
                    self.release(child)

    def register(self, handle, slots):
        if self.fails():
            return 0
        width = self.load(handle)
        assert width > 0
        assert all(self.load(slots, i*8)==0 for i in range(width))
        self.roots.update(slots+i*8 for i in range(width))
        return slots

    def unregister(self, slots):
        assert self.roots and min(self.roots)==slots
        assert all(self.load(slot)==0 for slot in self.roots)
        self.roots.clear()

    def take(self, slot, p):
        assert slot in self.roots
        old = self.load(slot)
        self.store(slot,0,p)
        self.release(old)
        if old and not p and self.cleanup_raises and self.sheltered:
            self.pending = 77

    def swap_exc(self, slot):
        assert slot in self.roots
        saved = self.load(slot)
        self.store(slot,0,self.pending or 0)
        self.pending = saved or None
        if not saved:
            self.sheltered = True

    def set_slots(self, d, k, v):
        assert {d,k,v} <= self.roots
        if self.fails():
            self.pending = 19
            return -1
        obj = self.objects[self.load(d)]
        key, child = self.load(k), self.load(v)
        label = self.objects[key]
        if label in obj:
            oldkey, oldchild = obj[label]
            self.release(oldchild)
            obj[label] = (oldkey,self.retain(child))
        else:
            obj[label] = (self.retain(key),self.retain(child))
        return 0

    def append(self, out, value):
        if self.fails():
            self.pending = 19
            return
        self.objects[out].append(self.retain(value))

    def append_slots(self, out, value, mask):
        assert out in self.roots and value in self.roots and mask == 0
        self.append(self.load(out),self.load(value))

    def raise_error(self, error):
        assert self.pending is None
        self.pending = error

    def value(self, p):
        value = self.objects[p]
        if isinstance(value,list):
            return [self.value(x) for x in value]
        if isinstance(value,dict):
            return {k:self.value(v) for k,(_,v) in value.items()}
        return value

    def balanced(self):
        assert not self.roots
        assert not self.allocations
        assert set(self.refs) == {self.input, *self.immortal.values()}


@pytest.mark.parametrize("text", [
    '{}', '{"n": [1, -2, true, false, null], "s":"x"}',
    '{"n": {"s": 0}, "n": {"d": 1}, "b": [], "a":1, "c":2, "z":3}',
    '{"s":"a\\n\\u2014\\u03b1\\uD83D\\uDE00\\u0000\\\\\\\""}',
])
def test_private_parser_admits_exact_supported_documents(monkeypatch,text):
    from pcc.runtime.py import py_json_runtime as r
    heap = _Heap(r,monkeypatch,text)
    result = r.pcc_json_try_loads_owned_ast(heap.input)
    assert heap.pending is None and result
    assert heap.value(result)==json.loads(text)
    assert all(x==0 for x in heap.dict_sizes)
    if '"n"' in text:
        assert ord("n") in heap.cached
    heap.release(result)
    heap.balanced()


@pytest.mark.parametrize("text", [
    'null','[]','{"n":1,}','{"n":[1,]}','{"n":1}garbage','{"n":01}',
    '{"n":1.5}','{"n":1e3}','{"n":NaN}','{"n":1152921504606846976}',
    '{"s":"\\uD800"}','{"s":"\\uDC00"}','{"s":"\\uD800\\u1234"}',
    '{"s":"é"}','{"s":"x\ny"}','{"s":"\\q"}','{"s":"\\u12"}',
    '{"x":'+ '['*70 + '0' + ']'*70 + '}',
])
def test_private_parser_unsupported_retires_partial_tree(monkeypatch,text):
    from pcc.runtime.py import py_json_runtime as r
    heap = _Heap(r,monkeypatch,text)
    result = r.pcc_json_try_loads_owned_ast(heap.input)
    assert result==heap.constants["py_None"] and heap.pending is None
    heap.release(result)
    heap.balanced()


def test_private_parser_allocation_and_mutation_failures_are_not_syntax_fallback(monkeypatch):
    from pcc.runtime.py import py_json_runtime as r
    text='{"n": [1, "v", {"s": "unicode\\u03b1"}], "s": false, "b": [], "x": 2}'
    baseline = _Heap(r,monkeypatch,text)
    result = r.pcc_json_try_loads_owned_ast(baseline.input)
    assert baseline.value(result)==json.loads(text)
    baseline.release(result)
    baseline.balanced()
    for fail_at in range(1,baseline.attempt+1):
        heap = _Heap(r,monkeypatch,text,fail_at)
        result = r.pcc_json_try_loads_owned_ast(heap.input)
        if result:
            # Only an unavailable ASCII image key may recover by allocation.
            assert heap.pending is None and heap.value(result)==json.loads(text)
            heap.release(result)
        else:
            assert heap.pending is not None
        heap.balanced()


def test_private_frontend_fallback_preserves_original_decoder(monkeypatch):
    from pcc.frontends.python import pipeline_ast_wire as wire
    seen=[]
    def original(text):
        seen.append(text)
        return json.loads(text)
    monkeypatch.setattr(wire, "sys", SimpleNamespace(implementation=SimpleNamespace(name="pcc")), raising=False)
    monkeypatch.setattr(wire, "_owned_ast_tape_scan", lambda text: None)
    monkeypatch.setattr(wire, "_try_owned_ast_json", lambda text: None, raising=False)
    monkeypatch.setattr(wire, "_maybe_trim_owned_ast_raw_highwater", lambda: None, raising=False)
    monkeypatch.setattr(wire, "json", SimpleNamespace(loads=original))
    text='{"schema":"pcc.frontends.python.py_ast.v2","files":[],"module":null}'
    assert wire._decode_py_ast_wire(text) is None and seen==[text]
    with pytest.raises(json.JSONDecodeError) as e:
        wire._decode_py_ast_wire('{"x":}')
    assert e.value.pos==5 and seen[-1]=='{"x":}'
    monkeypatch.setattr(wire, "_try_owned_ast_json", lambda text: json.loads(text))
    assert wire._decode_py_ast_wire(text) is None and len(seen)==2


@pytest.mark.parametrize("boundary", ["preexisting", "dict", "list"])
def test_private_parser_pending_and_silent_mutation_failures(monkeypatch, boundary):
    from pcc.runtime.py import py_json_runtime as r
    heap = _Heap(r, monkeypatch, '{"n": [1]}')
    if boundary == "preexisting":
        heap.pending = 42
    elif boundary == "dict":
        monkeypatch.setattr(r, "py_dict_set_slots", lambda d,k,v: -2)
    else:
        monkeypatch.setattr(r, "py_list_append_from_slots", lambda out,v,mask: None)
    assert r.pcc_json_try_loads_owned_ast(heap.input) == 0
    assert heap.pending == {"preexisting": 42, "dict": 7, "list": 19}[boundary]
    heap.balanced()


def test_private_parser_preserves_pending_operation_error_across_cleanup(monkeypatch):
    from pcc.runtime.py import py_json_runtime as r
    heap = _Heap(r, monkeypatch, '{"n": [1]}')
    heap.cleanup_raises = True
    def failed_insert(d,k,v):
        heap.pending = 19
        return -1
    monkeypatch.setattr(r, "py_dict_set_slots", failed_insert)
    assert r.pcc_json_try_loads_owned_ast(heap.input) == 0
    assert heap.pending == 19
    heap.balanced()


def test_private_parser_checks_success_with_pending_error(monkeypatch):
    from pcc.runtime.py import py_json_runtime as r
    heap = _Heap(r, monkeypatch, '{"n": [1]}')
    def constructed(expected):
        out = heap.new_dict(expected)
        heap.pending = 19
        return out
    monkeypatch.setattr(r, "py_dict_new_presized", constructed)
    assert r.pcc_json_try_loads_owned_ast(heap.input) == 0
    assert heap.pending == 19
    heap.balanced()


@pytest.mark.parametrize("reclaim,mapped,park,graph,pending,trim", [
    (7*1024*1024, 32*1024*1024, 0, 0, 0, False),
    (8*1024*1024, 64*1024*1024, 0, 0, 0, True),
    (8*1024*1024, 80*1024*1024, 0, 0, 0, False),
    (8*1024*1024, 0, 0, 0, 0, False),
    (35*1024*1024, 152*1024*1024, 1, 0, 0, False),
    (35*1024*1024, 152*1024*1024, 0, 1, 0, False),
    (35*1024*1024, 152*1024*1024, 0, 0, 1, False),
    (35*1024*1024, 152*1024*1024, 0, 0, 0, True),
])
def test_owned_ast_raw_highwater_requires_cold_safe_boundary(monkeypatch,reclaim,mapped,park,graph,pending,trim):
    from pcc.frontends.python import pipeline_ast_wire as wire
    seen=[]
    for name,value in (("_owned_ast_raw_reclaimable",reclaim),("_owned_ast_mapped",mapped),
                       ("_owned_ast_no_park",park),("_owned_ast_graph_depth",graph),
                       ("_owned_ast_pending",pending)):
        monkeypatch.setattr(wire,name,lambda value=value:value,raising=False)
    monkeypatch.setattr(wire,"_owned_ast_raw_trim",lambda:seen.append("trim") or 1,raising=False)
    wire._maybe_trim_owned_ast_raw_highwater()
    assert seen == (["trim"] if trim else [])


def test_owned_ast_raw_trim_only_on_admitted_native_parse(monkeypatch):
    from pcc.frontends.python import pipeline_ast_wire as wire
    seen=[]
    text='{"schema":"pcc.frontends.python.py_ast.v2","files":[],"module":null}'
    monkeypatch.setattr(wire,"sys",SimpleNamespace(implementation=SimpleNamespace(name="pcc")))
    monkeypatch.setattr(wire,"_maybe_trim_owned_ast_raw_highwater",lambda:seen.append("trim"),raising=False)
    monkeypatch.setattr(wire,"_owned_ast_tape_scan",lambda text:None)
    monkeypatch.setattr(wire,"_try_owned_ast_json",lambda text:json.loads(text))
    assert wire._decode_py_ast_wire(text) is None
    assert seen==["trim"]
    monkeypatch.setattr(wire,"_try_owned_ast_json",lambda text:None)
    assert wire._decode_py_ast_wire(text) is None
    assert seen==["trim"]
    monkeypatch.setattr(wire,"sys",SimpleNamespace(implementation=SimpleNamespace(name="cpython")))
    monkeypatch.setattr(wire,"_try_owned_ast_json",lambda text:pytest.fail("host must not call raw"))
    assert wire._decode_py_ast_wire(text) is None
    assert seen==["trim"]


def _tape_heap(monkeypatch, text, fail_at=0):
    import struct
    from pcc.runtime.py import py_json_runtime as runtime
    h = _Heap(runtime, monkeypatch, text, fail_at)
    def store32(p, offset, value):
        for i, byte in enumerate(struct.pack("<I", value & 0xffffffff)):
            h.raw[p+offset+i] = byte
    monkeypatch.setattr(runtime, "store_i32", store32)
    monkeypatch.setattr(runtime, "py_bytes_new",
                        lambda p, n: h.new(bytes(h.raw.get(p+i, 0) for i in range(n))),
                        raising=False)
    return runtime, h


def test_compact_ast_scanner_lexical_and_ownership(monkeypatch):
    import struct
    text = json.dumps({"schema": "pcc.frontends.python.py_ast.v2",
                       "files": [], "module": {"n": ["IntType", "int", 64, True]}})
    runtime, heap = _tape_heap(monkeypatch, text)
    p = runtime.pcc_json_scan_owned_ast_v2(heap.input)
    packed = heap.objects[p]
    assert struct.unpack_from("<8I", packed) == (844383056, 1, len(text), 6, 0, 1, 0, 0)
    assert len(packed) == 80
    assert heap.pending is None and not heap.allocations and not heap.roots
    for i, expected in enumerate(("IntType", "int", 64, True), 2):
        word, end = struct.unpack_from("<II", packed, 32+i*8)
        token = runtime.pcc_json_owned_ast_v2_token_value(
            heap.input, word & 536870911, end, word >> 29)
        assert heap.objects[token] == expected
        heap.release(token)
    heap.release(p)


def test_compact_ast_scanner_fallback_and_allocation_faults(monkeypatch):
    def doc(value):
        return json.dumps({"schema": "pcc.frontends.python.py_ast.v2",
                           "files": [], "module": value})
    bad = [doc({"b": [1]}), doc({"d": {"x": 1}}), doc(1.5), doc(2**60),
           doc("\ud800"), doc(1)+" trailing", doc(1)[:-1],
           doc(1).replace("1}", "01}"), doc(1).replace("v2", "v1"),
           doc(1).replace('"module"', '"module":0,"module"')]
    for text in bad:
        runtime, heap = _tape_heap(monkeypatch, text)
        p = runtime.pcc_json_scan_owned_ast_v2(heap.input)
        assert p == heap.constants["py_None"]
        assert heap.pending is None and not heap.allocations and not heap.roots
    text = doc([{"n": ["IntType", "int", 64, True]}]*160)
    runtime, heap = _tape_heap(monkeypatch, text)
    p = runtime.pcc_json_scan_owned_ast_v2(heap.input)
    assert p and isinstance(heap.objects[p], bytes)
    attempts = heap.attempt
    heap.release(p)
    assert attempts >= 2
    for fail in range(1, attempts+1):
        runtime, heap = _tape_heap(monkeypatch, text, fail)
        assert runtime.pcc_json_scan_owned_ast_v2(heap.input) == 0
        assert heap.pending is not None and not heap.allocations and not heap.roots


def test_compact_reader_full_preflight_and_constructor_parity(monkeypatch):
    """Execute the real scanner in the checked heap model and real reader."""
    import struct
    from pcc.frontends.python import pipeline_ast_wire as wire, py_ast
    from pcc.frontends.python.py_ast_contract import PY_AST_FIELD_NAME_OVERRIDES

    def scan(text):
        runtime, heap = _tape_heap(monkeypatch, text)
        result = runtime.pcc_json_scan_owned_ast_v2(heap.input)
        assert result and heap.pending is None
        value = heap.objects[result]
        heap.release(result)
        assert not heap.allocations and not heap.roots
        return value
    def doc(value, files=()):
        return json.dumps({"schema": wire._PY_AST_WIRE_SCHEMA_V2,
                           "files": list(files), "module": value})
    original = wire._decode_py_ast_wire_legacy
    def signature(fn, text):
        try:
            return ("ok", wire._py_ast_v2_payload(fn(text, "fixture")))
        except Exception as exc:
            return ("error", type(exc), str(exc),
                    getattr(exc, "doc", None), getattr(exc, "pos", None))

    monkeypatch.setattr(wire, "_owned_ast_tape_scan", scan)
    monkeypatch.setattr(wire, "_owned_ast_tape_u32",
                        lambda data, offset: struct.unpack_from("<I", data, offset)[0])
    monkeypatch.setattr(wire, "_owned_ast_tape_record",
                        lambda data, index: struct.unpack_from("<q", data, 32+8*index)[0])
    monkeypatch.setattr(wire, "_owned_ast_tape_text_len", lambda text: len(text.encode()))
    monkeypatch.setattr(wire, "_owned_ast_tape_token",
                        lambda text, start, end, kind: json.loads(text.encode()[start:end]))

    span = py_ast.SourceSpan("例.py", -1, 2, 3, 4)
    value = (py_ast.IntLit(span, py_ast.IntType("int", 128, False), 123),
             py_ast.StrLit(span, py_ast.StrType("custom"), "é\x00😀"),
             py_ast.Name(span, py_ast.DynType("dynamic"), "name"))
    canonical = json.dumps(wire._py_ast_v2_payload(value))
    valid = [canonical]
    for name, fields in PY_AST_FIELD_NAME_OVERRIDES.items():
        if name != "SourceSpan":
            valid.append(doc({"n": [name] + [None]*len(fields)}))
    invalid = [
        doc({"n": ["Unknown"]}), doc({"n": ["IntType", "int"]}),
        doc({"s": [0, 1, 2, 3, 4]}), doc({"s": [True, 1, 2, 3, 4]}, ["x"]),
        doc({"s": [0, True, 2, 3, 4]}, ["x"]), doc(1, ["x", "x"]),
        doc({"d": {"x": 1}}), doc({"b": [1, 2]}), doc(1.5), doc(2**60),
        doc("\ud800"), doc(1)+" trailing", doc(1)[:-1],
        doc({"n": ["Unknown"]})+" trailing", doc(1).replace("v2", "v1"),
        json.dumps({"module": 1, "files": [], "schema": wire._PY_AST_WIRE_SCHEMA_V2}),
        doc(1).replace('"module"', '"module":0,"module"'),
    ]
    expected = [signature(original, text) for text in valid+invalid]
    calls = []
    def fallback(text, path=""):
        calls.append(text)
        # Legacy provider semantics remain identical and are tested separately.
        return original(text, path)

    monkeypatch.setattr(wire, "_decode_py_ast_wire_legacy", fallback)
    monkeypatch.setattr(wire, "_try_owned_ast_json", lambda text: None)
    monkeypatch.setattr(wire, "sys", SimpleNamespace(implementation=SimpleNamespace(name="pcc")))
    for i, text in enumerate(valid+invalid):
        before = len(calls)
        assert signature(wire._decode_py_ast_wire, text) == expected[i]
        assert len(calls) == before + int(i >= len(valid)), text
    decoded = wire._decode_py_ast_wire(canonical)
    assert decoded[0].span.file is decoded[1].span.file is decoded[2].span.file
