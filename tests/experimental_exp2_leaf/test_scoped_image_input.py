"""Private artifact attestation never provides a general GC3 closed marker."""
from pathlib import Path
import ast,unittest
P=Path(__file__).resolve().parents[2]/'pcc/runtime/py/freestanding_exp2_window.py'
class Proof(unittest.TestCase):
 def setUp(self):
  self.mem={'pcc_exp2_image_witness':-1};self.env={'PCC_GC_BACKEND':'3','PATH':'/nonexistent'};self.args=('handler','100','0','100','--samples','4');self.hash=1;self.sys_platform=b'linux';self.revoked=0;self.backend=3
  self.held=7;self.live_leases=[0];self.calls=[]
  e={}
  def extern(name,*spec):
   def fn(*args):
    self.calls.append(name)
    if name=='pcc_gc_foreign_lease_count_quiescent':return self.live_leases.pop(0) if len(self.live_leases)>1 else self.live_leases[0]
    if name in ('pcc_stop_the_world','pcc_resume_world'):return 0
    if name=='pcc_gc_backend':return self.backend
    if name=='py_program_mode':return 0
    if name=='py_program_argc':return len(self.args)
    if name=='py_program_argv':return self.args[args[0]].encode()
    if name=='pcc_platform_env_snapshot_count':return len(self.env)
    if name=='pcc_platform_getenv':return self.env.get(args[0].decode(),'').encode()
    if name=='pcc_exp2_exposure_is_revoked':return self.revoked
    if name=='pcc_exp2_own_image_matches_seal':return self.hash
    if name=='pcc_thread_world_stop_epoch_held':return self.held
    return 1
   return fn
  def cas(k,off,expect,val,*_):
   actual=self.mem.get(k,0)
   if actual==expect:self.mem[k]=val
   return actual
  e.update(i64=int,extern=extern,c_ptr=int,c_int64=int,c_int32=int,c_void=object,c_abi_export=lambda n:lambda f:f,define_global_i64=lambda n,v:None,global_addr=lambda n:n,atomic_load_i32=lambda k,o,*_:self.mem.get(k,0),atomic_load_i64=lambda k,o,*_:self.mem.get(k,0),atomic_store_i64=lambda k,o,v,*_:self.mem.__setitem__(k,v),atomic_cas_i64=cas,null=lambda:0,function_addr=lambda n:n,ptr_is_null=lambda p:not p,cstr=lambda s:s.encode(),load_i8=lambda s,i:s[i] if i<len(s) else 0,target_sys_platform=lambda:self.sys_platform)
  tree=ast.parse(P.read_text());tree.body=[x for x in tree.body if not isinstance(x,(ast.Import,ast.ImportFrom))];exec(compile(tree,str(P),'exec'),e);self.e=e
 def test_exact_input_only(self):
  self.assertEqual(self.e['pcc_exp2_window_control'](4),1)
  self.assertEqual(self.e['pcc_exp2_window_domain_ok'](),1)
  self.assertNotIn('pcc_gc_backend3_closed_young_domain',self.mem)
  self.revoked=1;self.assertEqual(self.e['pcc_exp2_window_domain_ok'](),0)
 def test_stopped_install_denies_one_live_mode_shard_or_sentinel_and_never_admits(self):
  for count in (1,-1,-2):
   self.setUp();self.assertEqual(self.e['pcc_exp2_window_control'](4),1)
   self.live_leases=[count];self.assertEqual(self.e['pcc_exp2_window_control'](1),-15)
   self.assertNotIn('pcc_exp2_install_exposure_hook',self.calls)
   self.assertNotIn('pcc_exp2_process_enable',self.calls)
   self.assertEqual(self.e['pcc_exp2_window_control'](2),-10)
   self.assertIn('pcc_resume_world',self.calls)
 def test_held_cut_rechecks_quiescence_before_enable(self):
  self.assertEqual(self.e['pcc_exp2_window_control'](4),1)
  self.live_leases=[0,1];self.assertEqual(self.e['pcc_exp2_window_control'](1),-15)
  self.assertNotIn('pcc_exp2_process_enable',self.calls)
 def test_only_sealed_image_may_classify_relocated_static_origins(self):
  self.mem['pcc_gc_backend3_closed_young_domain']=1
  self.assertEqual(self.e['pcc_exp2_window_domain_ok'](),1)
  self.assertEqual(self.e['pcc_exp2_window_image_verified'](),0)
  self.mem['pcc_gc_backend3_closed_young_domain']=0
  self.assertEqual(self.e['pcc_exp2_window_control'](4),1)
  self.assertEqual(self.e['pcc_exp2_window_image_verified'](),1)
  self.revoked=1;self.assertEqual(self.e['pcc_exp2_window_image_verified'](),0)
 def test_changed_env_or_worker_or_archive_never_admits(self):
  self.hash=0;self.assertLess(self.e['pcc_exp2_window_control'](4),1);self.assertEqual(self.e['pcc_exp2_window_domain_ok'](),0)
  for a in ['--summary','--samples-fake','--samples\0junk']:
   if '\0' in a:continue # impossible argv element at exec
   self.setUp();self.args=('handler','100','0','100',a,'4');self.assertLess(self.e['pcc_exp2_window_control'](4),1)
  self.setUp();self.env['LD_PRELOAD']='x';self.assertLess(self.e['pcc_exp2_window_control'](4),1)
  self.setUp();self.env['PATH']='/tmp';self.assertLess(self.e['pcc_exp2_window_control'](4),1)
  for w in ('2','04','4x'):
   self.setUp();self.args=self.args[:-1]+(w,);self.assertLess(self.e['pcc_exp2_window_control'](4),1)
 def test_preexisting_domain_revocation_has_priority(self):
  self.mem['pcc_gc_backend3_closed_young_domain']=2;self.assertLess(self.e['pcc_exp2_window_control'](4),1);self.assertEqual(self.e['pcc_exp2_window_domain_ok'](),0)
if __name__=='__main__':unittest.main()
