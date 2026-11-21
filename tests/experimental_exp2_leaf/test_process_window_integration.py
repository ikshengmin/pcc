"""Private experiment: detect real allocator admission, stop owner, and app boundaries."""
import ast
from pathlib import Path
import unittest
R=Path(__file__).resolve().parents[2]/"pcc"/"runtime"/"py"
def fn(name,file):
  return next(x for x in ast.parse((R/file).read_text()).body if isinstance(x,ast.FunctionDef) and x.name==name)
def selected_calls(node):
  return [(n.lineno,n.func.id) for n in ast.walk(node) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
class Cuts(unittest.TestCase):
  def test_allocator_checks_opt_in_and_preserves_stock_fallback(self):
    f=fn("pcc_gc_alloc","py_obj.py")
    a=dict((n,l) for l,n in selected_calls(f))
    self.assertLess(a["pcc_thread_safepoint"],a["pcc_exp2_process_take"])
    self.assertLess(a["pcc_exp2_window_active"],a["pcc_exp2_process_take"])
    self.assertLess(a["pcc_exp2_process_take"],a["pcc_gc_register_object_cell"])
    qualified=next(x for x in ast.walk(f) if isinstance(x,ast.If) and "PY_TYPE_FLOAT" in ast.unparse(x.test) and "PY_TYPE_STR" in ast.unparse(x.test))
    self.assertIn("backend == 3",ast.unparse(qualified.test));self.assertIn("flags == 0",ast.unparse(qualified.test))
    self.assertIn("pcc_exp2_process_take",dict((n,l) for l,n in selected_calls(qualified)))
  def test_managed_recognition_is_exact_after_window_closes(self):
    f=fn("pcc_gc_pointer_is_managed","py_gc_backend.py")
    a=dict((n,l) for l,n in selected_calls(f))
    self.assertNotIn("pcc_exp2_window_active",a)
    self.assertLess(a["pcc_exp2_process_is_start"],a["pcc_gc_granule_is_object_start"])
  def test_minor_has_real_claim_after_unlock_before_seed(self):
    f=fn("_pcc_gc_complete_mark_cycle_seed","freestanding_gc_incremental_concurrent_scheduler.py")
    calls=sorted(selected_calls(f)); minor=next(l for l,n in calls if n=="pcc_exp2_window_stop_minor")
    self.assertTrue(any(l<minor and n=="pcc_py_gc_minor_graph_unlock" for l,n in calls))
    self.assertTrue(any(l>minor and n=="pcc_gc_seed_roots" for l,n in calls))
    self.assertTrue(any(l==minor and n=="pcc_thread_world_stop_epoch_held" for l,n in calls))
if __name__=="__main__":unittest.main()
