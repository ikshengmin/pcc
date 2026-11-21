"""Test exact old-shape and first-failure code in an audited memory model."""
import ast
from pathlib import Path
import unittest

SRC = Path(__file__).resolve().parents[2] / "pcc/runtime/py"


class Memory:
    def __init__(self):
        self.words = {}
        self.next = 0x10000

    def reserve(self, n):
        p = self.next
        self.next += 0x400
        for i in range(n):
            self.words[p + i] = 0
        return p

    def get(self, p, o, *_):
        if (p + o) not in self.words:
            raise AssertionError("read beyond proved storage")
        return self.words[p + o]

    def put(self, p, o, v, *_):
        self.words[p + o] = v


def load_functions(file, env):
    src = SRC / file
    tree = ast.parse(src.read_text(), filename=str(src))
    tree.body = [
        x for x in tree.body
        if isinstance(x, ast.FunctionDef) and
        (x.name.startswith("_old_") or x.name.startswith("pcc_exp2_"))
    ]
    env.update(i64=int, c_abi_export=lambda _: lambda f: f)
    exec(compile(tree, str(src), "exec"), env)


class OldShape(unittest.TestCase):
    def setUp(self):
        self.m = m = Memory()
        self.o, self.cls, self.node, self.cn = [m.reserve(n) for n in (64,120,40,40)]
        self.audit = m.reserve(96)
        self.visit_counts = 0
        self.maps = {self.cls: self.cn}
        self.tag = 114   # This fixture uses an instance-domain tag, not its name.
        self.tags = {
            "object.type.user_class_start": 104,
            "object.type.class": 10, "object.type.instance": 11, "object.type.valuebox": 200,
            "object.header.type_tag_offset": 8, "object.header.flags_offset": 12,
        }
        def abi(name):
            if name not in self.tags:
                self.tags[name] = -1000 - len(self.tags)
            return self.tags[name]
        m.put(self.node, 0, self.o)
        m.put(self.node, 8, 64)
        m.put(self.o, 8, self.tag)
        m.put(self.cn, 0, self.cls)
        m.put(self.cn, 8, 120)
        m.put(self.cls, 8, 10)
        m.put(self.cls, 72, 4)
        m.put(self.cls, 88, 64)
        self.e = dict(
            abi_constant=abi, is_tagged_int=lambda p: p & 1,
            ptr_eq=lambda a,b: a == b, ptr_is_null=lambda p: p == 0,
            load_i32=m.get,load_i64=m.get,load_ptr=m.get,
            store_i64=m.put,store_ptr=m.put,stack_alloc=m.reserve,
            _OLD_LIMIT=1048576, _old_index=lambda cls: self.maps.get(cls,0),
            _old_first=lambda: self.node, _old_next=lambda _: 0,
            _old_slice=self.visit,
        )
        for fn in ("_old_domain","_old_exact_only"):
            self.e[fn] = lambda _: 1
        load_functions("freestanding_gc_object_slots.py", self.e)
        self.e["_old_domain"] = self.e["_old_exact_only"] = lambda _: 1

    def visit(self, obj, cursor, budget, callback, state, curs):
        self.assertEqual((obj,cursor,budget),(self.o,0,64))
        self.visit_counts += 1
        self.m.put(curs,0,-1)
        return 1

    def call(self, expected_clause, size=64, flags=0, status=-7):
        self.m.put(self.node,8,size)
        self.m.put(self.o,12,flags)
        self.visit_counts = 0
        self.assertEqual(self.e["_old_shape"](self.o,self.tag,size,flags),status)
        rv = self.e["pcc_exp2_visit_stopped_old_edges"](3,lambda *_:None,0,self.audit)
        self.assertEqual(rv,status)
        self.assertEqual(self.m.get(self.audit,24),status)
        self.assertEqual(self.m.get(self.audit,80),expected_clause)
        self.assertEqual(self.m.get(self.audit,88),0)
        if status < 0:
            self.assertEqual((self.m.get(self.audit,16),self.m.get(self.audit,64),
                              self.m.get(self.audit,72),self.visit_counts),(self.tag,size,flags,0))
        else:
            self.assertEqual(self.visit_counts,1)

    def test_normal_zero_filled_instance_then_finished(self):
        self.call(21)
        self.m.put(self.o,16,self.cls)
        self.call(0,status=1)

    def test_general_size_flags_and_descriptor_provenance(self):
        self.call(1,size=8,flags=524288)
        self.call(2,flags=524288)
        self.call(20,size=16)
        self.m.put(self.o,16,3)
        self.call(22)
        self.m.put(self.o,16,0x6000)
        self.call(23)  # unknown is not read at all
        self.m.put(self.o,16,self.cls)
        self.m.put(self.cn,0,0x6000)
        self.call(24)
        self.m.put(self.cn,0,self.cls)
        self.m.put(self.cn,32,1)
        self.call(25)
        self.m.put(self.cn,32,0)
        self.m.put(self.cn,8,16)
        self.call(26)
        self.m.put(self.cn,8,120)
        self.m.put(self.cls,8,6)
        self.call(27)

    def test_size_and_field_failures_are_distinct(self):
        self.m.put(self.o,16,self.cls)
        for sz,fields,want in ((23,4,28),(25,4,29),(72,4,30),(64,-1,31),
                               (64,1048576,32),(64,5,33)):
            with self.subTest(sz=sz,fields=fields):
                self.m.put(self.cls,88,sz)
                self.m.put(self.cls,72,fields)
                self.call(want)
        self.m.put(self.cls,88,64+8*1048576)
        self.m.put(self.cls,72,4)
        self.call(34,size=64+8*1048576)
        self.tag=32
        self.m.put(self.o,8,self.tag)
        self.call(0,status=-6)


class FirstRecord(unittest.TestCase):
    def test_first_shape_record_stays_paired_and_does_not_change_legacy(self):
        m=Memory()
        e={}
        state={}
        next_bad=[-14,-2,12,2,0]
        def aget(g,o,*_):return state.get((g,o),0)
        def aset(g,o,v,*_):state[g,o]=v
        def cas(g,o,old,v,*_):
            x=aget(g,o)
            if x==old:aset(g,o,v)
            return x
        def add(_,g,o,v,*args):aset(g,o,aget(g,o)+v)
        def walk(epoch,callback,context,a):
            rc,tag,size,flags,clause=next_bad
            for off,v in zip((16,24,64,72,80),(tag,rc,size,flags,clause),strict=True):
                m.put(a,off,v)
            return rc
        e.update(global_addr=lambda g:g,atomic_load_i64=aget,atomic_store_i64=aset,
                 atomic_cas_i64=cas,atomic_rmw_i64=add,stack_alloc=m.reserve,
                 load_i64=m.get,store_i64=m.put,function_addr=lambda f:f,
                 atomic_load_i32=lambda *_:1,pcc_exp2_exposure_is_revoked=lambda:0,
                 pcc_exp2_process_metric=lambda _: -999,
                 pcc_thread_world_stop_epoch_held=lambda:3,
                 pcc_exp2_visit_stopped_old_edges=walk)
        load_functions("freestanding_exp2_window.py",e)
        e["pcc_exp2_window_active"]=lambda:1
        def metric(*indices):return [e["pcc_exp2_window_metric"](i) for i in indices]
        call=e["pcc_exp2_window_checked_old"]
        self.assertEqual(metric(80,81,82,83),[0,0,0,0])
        self.assertEqual(call(3,0),0)
        self.assertEqual(metric(31,32,80,81,82,83),[-14,-2,0,0,0,0])
        next_bad[:]=[-7,114,80,0,21]
        self.assertEqual(call(3,0),0)
        self.assertEqual(metric(31,32,80,81,82,83),[-14,-2,21,80,0,114])
        next_bad[:]=[-7,12,64,524288,2]
        self.assertEqual(call(3,0),0)
        self.assertEqual(metric(31,32,80,81,82,83),[-14,-2,21,80,0,114])


if __name__=="__main__":
    unittest.main()
