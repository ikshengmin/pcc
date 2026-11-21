"""Portable checks over the exact freestanding window source, no native perf claims."""
from pathlib import Path
import ast,unittest
p=Path(__file__).resolve().parents[2]/'pcc/runtime/py/freestanding_exp2_window.py'
class Win(unittest.TestCase):
 def setUp(self):
  self.mem={};self.calls=[];self.epoch=3;self.closed=1;self.revoked=0; self.setupok=1; self.old=1;self.tag=0
  def ld(p,o=0,order=None):
   if p=='pcc_gc_backend3_closed_young_domain':return self.closed
   return self.mem.get((p,o),0)
  def st(p,o,v,order=None):self.mem[p,o]=v
  def cas(p,o,expected,new,*_):
   prev=ld(p,o)
   if prev==expected:st(p,o,new)
   return prev
  def ext(name,sig,ret):
   def cb(*args):
    self.calls.append((name,args))
    if name=='pcc_gc_backend':return 3
    if name=='pcc_stop_the_world':return 0
    if name=='pcc_resume_world':return 0
    if name=='pcc_thread_world_stop_epoch_held':return self.epoch
    if name=='pcc_exp2_exposure_is_revoked':return self.revoked
    if name in ('pcc_exp2_install_exposure_hook','pcc_exp2_process_register_gate','pcc_exp2_process_enable'):return self.setupok
    if name=='pcc_exp2_visit_stopped_old_edges':
     st(args[3],0,12); st(args[3],8,7); st(args[3],16,self.tag);st(args[3],24,-3 if self.tag else 0); return self.old
    if name=='pcc_exp2_process_minor_held': return 2
    if name=='pcc_exp2_process_metric':return 17
   return cb
  e=dict(extern=ext,global_addr=lambda name:name,define_global_i64=lambda *a:None,
         atomic_load_i32=ld,atomic_load_i64=ld,atomic_store_i64=st,atomic_cas_i64=cas,
         atomic_rmw_i64=lambda op,p,o,v,*_:st(p,o,ld(p,o)+v), stack_alloc=lambda n:4096,
         function_addr=lambda name:name, load_i64=ld,store_i64=st,ptr_is_null=lambda p:p==0,
         null=lambda:0,i64=int,c_int64=int,c_void=object,c_ptr=int,c_abi_export=lambda *a:lambda f:f)
  tree=ast.parse(p.read_text());tree.body=[x for x in tree.body if not isinstance(x,(ast.Import,ast.ImportFrom))]
  exec(compile(tree,str(p),'exec'),e);self.e=e
 def runfn(self,s,*a):return self.e['pcc_exp2_'+s](*a)
 def test_never_assumes_a_domain(self):
  self.closed=2; self.assertLess(self.runfn('window_control',1),0)
  self.assertFalse(any(name=='pcc_exp2_process_enable' for name,_ in self.calls))
 def test_optin_sequence(self):
  self.assertEqual(self.runfn('window_control',1),1);self.assertEqual(self.runfn('window_active'),0)
  self.assertEqual(self.runfn('window_control',2),1);self.assertEqual(self.runfn('window_active'),1)
  self.assertEqual(self.runfn('window_checked_old',3,0),1);self.assertEqual(self.runfn('window_stop_minor',3),2)
  self.assertEqual(self.runfn('window_control',3),1);self.assertEqual(self.runfn('window_active'),0)
  self.assertEqual(self.runfn('window_stop_minor',3),-1)
 def test_unknown_or_stale_cut_denies(self):
  self.runfn('window_control',1);self.runfn('window_control',2);self.old=0;self.tag=32
  self.assertLessEqual(self.runfn('window_checked_old',3,0),0);self.assertEqual(self.runfn('window_metric',32),32)
  self.assertLessEqual(self.runfn('window_checked_old',2,0),0);self.revoked=1
  self.assertLessEqual(self.runfn('window_checked_old',3,0),0)
if __name__=='__main__': unittest.main()
