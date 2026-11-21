"""Private experimental kernel: shared host/native semantic vector, not a perf gate."""
import importlib.util
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

S = Path(__file__).resolve().parents[2] / "pcc/runtime/py/freestanding_exp2_leaf_nursery.py"


class Heap:
    def __init__(self):
        self.b = bytearray(1 << 20)
        self.loads = 0

    def load(self, p, n):
        self.loads += 1
        if not (0 < p and p + n <= len(self.b)):
            raise AssertionError(("outside", p, n))
        return int.from_bytes(self.b[p:p+n], "little", signed=True)
    def store(self,p,n,v):
        if not (0 < p and p + n <= len(self.b)):
            raise AssertionError(("outside",p,n))
        self.b[p:p+n] = int(v).to_bytes(n,"little",signed=True)


class Nursery(unittest.TestCase):
    def setUp(self):
        self.h = h = Heap()
        env = dict(abi_constant=lambda name:{"object.type.str":4,"object.type.float":3}[name],
                   memset=lambda p,val,n:h.b.__setitem__(slice(p,p+n),bytes([val])*n),
                   define_global_i64=lambda *x:None, global_addr=lambda name:9000,
                   atomic_store_i64=lambda p,o,v,mo:h.store(p+o,8,v),
                   atomic_load_i64=lambda p,o,mo:h.load(p+o,8),
                   define_thread_local_ptr_null=lambda *x:None, c_int64=int, extern=lambda *x:None, i64=int, c_ptr=int, null=lambda:0, ptr_is_null=lambda p:p==0,
                   ptr_to_int=int, int_to_ptr=int, ptr_add=lambda p,n:p+n, ptr_diff=lambda p,q:p-q,
                   c_abi_export=lambda *x:lambda f:f, load_ptr=lambda p,o:h.load(p+o,8),
                   store_ptr=lambda p,o,v:h.store(p+o,8,v), load_i32=lambda p,o:h.load(p+o,4),
                   store_i32=lambda p,o,v:h.store(p+o,4,v), load_i64=lambda p,o:h.load(p+o,8),
                   store_i64=lambda p,o,v:h.store(p+o,8,v))
        import ast
        a=ast.parse(S.read_text())
        a.body=[x for x in a.body if not isinstance(x,(ast.Import,ast.ImportFrom))]
        exec(compile(a,str(S),"exec"),env)
        self.e=env
        self.state,self.base,self.pages,self.rem,self.n=8,65536,512,1024,5
        self.assertEqual(self.call("init",self.state,self.base,self.pages,self.n,self.rem,8),1)
    def call(self,name,*args):
        return self.e['pcc_exp2_leaf_'+name](*args)
    def test_bump_minor(self):
        s,b,p=self.state,self.base,self.pages
        # A union of local roots, conservative interior roots, and the *current*
        # targets of dirty stable strong slots. No stale remembered values.
        a=[self.call("take",s,64) for _ in range(5*64)]
        self.assertEqual(len(set(a)),320)
        self.assertEqual(a[0],b)
        self.assertEqual(a[-1],b+5*4096-64)
        self.assertEqual(self.call("take",s,41),0)
        self.assertEqual(self.call("take",s,65),0)
        self.assertEqual(self.call("is_start",s,b+1),0)
        self.assertEqual(self.call("is_start",s,b+64),1)
        self.assertEqual(self.call("remember",s,1320),1)
        self.assertEqual(self.call("remember",s,1320),1)
        self.assertEqual(self.call("remember",s,1328),1)
        self.h.store(1320,8,a[5])
        self.h.store(1328,8,a[250])
        self.h.store(1328,8,0)
        # Current independent direct and interior roots protect corresponding slab.
        self.h.store(1400,8,a[100]);self.h.store(1408,8,a[192]+63)
        self.assertEqual(self.call("minor",s,1400,2),2)
        self.assertEqual(self.call("is_start",s,a[130]),0)
        self.assertEqual(self.call("is_start",s,a[260]),0)
        self.assertEqual(self.call("is_start",s,a[100]),1)
        self.assertEqual(self.call("is_start",s,a[192]),1)
        self.assertEqual(self.call("is_start",s,a[5]),1)
        self.assertEqual(self.call("take",s,41),a[128])
        # Promoted pages are not rescanned or moved on a second minor.
        self.assertEqual(self.call("minor",s,0,0),1)
        self.assertEqual(self.call("is_start",s,a[5]),1)
    def test_remember_overflow_must_fail_closed(self):
        s=self.state
        ptr=self.call("take",s,57)
        for i in range(8):self.assertEqual(self.call("remember",s,1512+8*i),1)
        self.assertEqual(self.call("remember",s,1600),0)
        self.assertEqual(self.call("minor",s,0,0),-1)
        self.assertEqual(self.call("is_start",s,ptr),1)
    def test_unallocated_tail_not_root(self):
        s,b=self.state,self.base
        self.assertEqual(self.call("take",s,64),b)
        self.h.store(1600,8,b+65)
        self.assertEqual(self.call("minor",s,1600,1),1)
        self.assertEqual(self.call("is_start",s,b),0)
    def test_requested_bytes_and_page_retention(self):
        s,b=self.state,self.base
        self.call("take",s,41); self.call("take",s,60)
        self.assertEqual(self.h.load(s+112,8),101)
        self.h.store(1400,8,b)
        self.assertEqual(self.call("minor",s,1400,1),0)
        # Both fixed cells stay allocated when the first cell is live. This
        # is a high-water count, not a claim both cells were individually live.
        self.assertEqual(self.h.load(s+120,8),2)
    def test_invalid_configuration(self):
        self.assertEqual(self.call("init",self.state,0,self.pages,5,self.rem,8),0)
        self.assertEqual(self.call("take",self.state,0),0)
        self.assertEqual(self.call("minor",self.state,0,1),-1)
        self.assertEqual(self.call("minor",self.state,0,-1),-1)
if __name__=='__main__': unittest.main()
