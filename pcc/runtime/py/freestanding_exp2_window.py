"""Private in-process research controller: opt-in scope, fail-closed old/foreign cut.

This reference mode scans all otherwise uncertified old owners. It establishes
a correct upper bound on visited old slots, not that the dirty-only path works.
No request or application code is called while the world is stopped.
"""
from pcc import i64
from pcc.extern import c_abi_export,c_ptr,c_int64,c_int32,c_void,extern
from pcc.unsafe import (
    cstr,load_i8,ptr_is_null,target_sys_platform,
    atomic_load_i32,atomic_load_i64,atomic_store_i64,atomic_cas_i64,atomic_rmw_i64,
    define_global_i64,global_addr,function_addr,stack_alloc,load_i64,store_i64,null,
)
__pcc_freestanding__=True
pcc_expr_program_argc=extern("py_program_argc",(),c_int64)
pcc_expr_program_argv=extern("py_program_argv",(c_int64,),c_ptr)
pcc_expr_program_mode=extern("py_program_mode",(),c_int32)
pcc_env_count=extern("pcc_platform_env_snapshot_count",(),c_int64)
pcc_env_get=extern("pcc_platform_getenv",(c_ptr,),c_ptr)
pcc_exp2_old_exact_metric=extern("pcc_exp2_old_exact_metric",(c_int64,),c_int64)
pcc_exp2_own_image_matches_seal=extern("pcc_exp2_own_image_matches_seal",(),c_int64)
pcc_gc_backend=extern("pcc_gc_backend",(),c_int64)
pcc_exp2_total_mode_leases=extern("pcc_gc_foreign_lease_count_quiescent",(),c_int64)
pcc_stop_the_world=extern("pcc_stop_the_world",(),c_int64)
pcc_resume_world=extern("pcc_resume_world",(),c_int64)
pcc_thread_world_stop_epoch_held=extern("pcc_thread_world_stop_epoch_held",(),c_int64)
pcc_exp2_exposure_is_revoked=extern("pcc_exp2_exposure_is_revoked",(),c_int64)
pcc_exp2_install_exposure_hook=extern("pcc_exp2_install_exposure_hook",(c_ptr,),c_int64)
pcc_exp2_process_expose=extern("pcc_exp2_process_expose",(c_ptr,c_int64),c_int64)
pcc_exp2_process_mark=extern("pcc_exp2_process_mark",(c_ptr,c_ptr),c_void)
pcc_exp2_process_enable=extern("pcc_exp2_process_enable",(c_int64,c_int64),c_int64)
pcc_exp2_process_register_gate=extern("pcc_exp2_process_register_gate",(c_ptr,c_ptr),c_int64)
pcc_exp2_process_minor_held=extern("pcc_exp2_process_minor_held",(c_int64,),c_int64)
pcc_exp2_visit_stopped_old_edges=extern("pcc_exp2_visit_stopped_old_edges",(c_int64,c_ptr,c_ptr,c_ptr),c_int64)
pcc_exp2_process_metric=extern("pcc_exp2_process_metric",(c_int64,),c_int64)
# 0 uninitialized, -1 performing setup, <=-2 refused, 1 prepared,
# 2 timed/active, 3 stopped. The single-per-process experiment never reopens.
define_global_i64("pcc_exp2_window_state",0)
# Successful only on one independently approved, normalized-hash ELF.
define_global_i64("pcc_exp2_image_witness",-1)
define_global_i64("pcc_exp2_window_first_bad_tag",0)
define_global_i64("pcc_exp2_window_first_bad_reason",0)
define_global_i64("pcc_exp2_window_old_owners",0)
define_global_i64("pcc_exp2_window_old_slots",0)
define_global_i64("pcc_exp2_window_attempts",0)
define_global_i64("pcc_exp2_window_refused",0)


@c_abi_export("pcc_exp2_window_mark_cb")
def pcc_exp2_window_mark_cb(word, context)->None:
    pcc_exp2_process_mark(word, context)


@c_abi_export("pcc_exp2_window_expose_cb")
def pcc_exp2_window_expose_cb(word, reason:i64)->i64:
    return pcc_exp2_process_expose(word,reason)


@c_abi_export("pcc_exp2_window_cstr_equal")
def _window_equal(a,b)->i64:
    if ptr_is_null(a) or ptr_is_null(b):
        return 0
    i:i64=0
    while i<64:
        x:i64=load_i8(a,i)
        if x!=load_i8(b,i):
            return 0
        if x==0:
            return 1
        i+=1
    return 0


@c_abi_export("pcc_exp2_window_inputs")
def _window_inputs()->i64:
    # Allow only this in-process experiment. No path-, environment-, or
    # user-controlled input by itself can grant the separate image witness.
    if (_window_equal(target_sys_platform(),cstr("linux"))!=1 or
        pcc_expr_program_mode()!=0 or pcc_expr_program_argc()!=6 or pcc_env_count()!=2):
        return 0
    if (_window_equal(pcc_env_get(cstr("PATH")),cstr("/nonexistent"))!=1 or
        _window_equal(pcc_env_get(cstr("PCC_GC_BACKEND")),cstr("3"))!=1):
        return 0
    return 1 if (
        _window_equal(pcc_expr_program_argv(1),cstr("100"))==1 and
        _window_equal(pcc_expr_program_argv(2),cstr("0"))==1 and
        _window_equal(pcc_expr_program_argv(3),cstr("100"))==1 and
        _window_equal(pcc_expr_program_argv(4),cstr("--samples"))==1 and
        (_window_equal(pcc_expr_program_argv(5),cstr("1"))==1 or
         _window_equal(pcc_expr_program_argv(5),cstr("4"))==1)) else 0


@c_abi_export("pcc_exp2_window_image_verified")
def pcc_exp2_window_image_verified()->i64:
    # Static origin entries are only valid in the independently verified ELF.
    # Generic closed-world tests alone never certify a file's symbol identities.
    if (pcc_exp2_exposure_is_revoked()!=0 or
        atomic_load_i32(global_addr("pcc_gc_backend3_closed_young_domain"),0,"acquire")==2):
        return 0
    return 1 if atomic_load_i64(global_addr("pcc_exp2_image_witness"),0,"acquire")==1 else 0


@c_abi_export("pcc_exp2_window_domain_ok")
def pcc_exp2_window_domain_ok()->i64:
    state:i64=atomic_load_i32(global_addr("pcc_gc_backend3_closed_young_domain"),0,"acquire")
    if state==2 or pcc_exp2_exposure_is_revoked()!=0:
        return 0
    if state==1:
        return 1
    # An exact, independently qualified diagnostic does NOT make GC3's
    # ordinary compiler/import domain closed. No generic flag is written.
    if state==0 and atomic_load_i64(global_addr("pcc_exp2_image_witness"),0,"acquire")==1:
        return 1
    return 0


@c_abi_export("pcc_exp2_window_active")
def pcc_exp2_window_active()->i64:
    return 1 if atomic_load_i64(global_addr("pcc_exp2_window_state"),0,"acquire")==2 else 0


@c_abi_export("pcc_exp2_window_checked_old")
def pcc_exp2_window_checked_old(epoch:i64,context)->i64:
    if (epoch<=0 or pcc_thread_world_stop_epoch_held()!=epoch or
        pcc_exp2_window_active()!=1 or pcc_exp2_exposure_is_revoked()!=0 or
        pcc_exp2_window_domain_ok()!=1):
        return 0
    audit=stack_alloc(96)
    i:i64=0
    while i<12:
        store_i64(audit,i*8,0)
        i+=1
    result:i64=pcc_exp2_visit_stopped_old_edges(epoch,function_addr("pcc_exp2_window_mark_cb"),context,audit)
    atomic_rmw_i64("add",global_addr("pcc_exp2_window_old_owners"),0,load_i64(audit,0),"relaxed")
    atomic_rmw_i64("add",global_addr("pcc_exp2_window_old_slots"),0,load_i64(audit,8),"relaxed")
    if (result!=1 or pcc_exp2_exposure_is_revoked()!=0
        or pcc_thread_world_stop_epoch_held()!=epoch or pcc_exp2_window_active()!=1
        or pcc_exp2_window_domain_ok()!=1):
        tag:i64=load_i64(audit,16)
        reason:i64=load_i64(audit,24)
        if tag!=0:
            atomic_cas_i64(global_addr("pcc_exp2_window_first_bad_tag"),0,0,tag,"acq_rel","acquire")
        if reason!=0:
            atomic_cas_i64(global_addr("pcc_exp2_window_first_bad_reason"),0,0,reason,"acq_rel","acquire")
        return 0
    return 1


@c_abi_export("pcc_exp2_window_control")
def pcc_exp2_window_control(mode:i64)->i64:
    if pcc_gc_backend()!=3:
        return 0
    state=global_addr("pcc_exp2_window_state")
    if mode==4:
        # Before spawning the benchmark virtual thread or warming up. Always
        # zero-by-default and single-use. Refuse unsealed apps without a cut.
        witness=global_addr("pcc_exp2_image_witness")
        if atomic_cas_i64(witness,0,-1,-2,"acq_rel","acquire")!=-1:
            return -13
        eligible:i64=0
        if (atomic_load_i32(global_addr("pcc_gc_backend3_closed_young_domain"),0,"acquire")==0 and
            pcc_exp2_exposure_is_revoked()==0 and _window_inputs()==1):
            eligible=pcc_exp2_own_image_matches_seal()
        atomic_store_i64(witness,0,eligible,"release")
        return eligible
    if mode==2:
        return 1 if atomic_cas_i64(state,0,1,2,"acq_rel","acquire")==1 else -10
    if mode==3:
        return 1 if atomic_cas_i64(state,0,2,3,"acq_rel","acquire")==2 else -11
    if mode!=1 or atomic_cas_i64(state,0,0,-1,"acq_rel","acquire")!=0:
        return -12
    if (atomic_load_i64(global_addr("pcc_exp2_image_witness"),0,"acquire")==1 and _window_inputs()!=1):
        atomic_store_i64(state,0,-14,"release")
        return -14
    if pcc_stop_the_world()!=0:
        atomic_store_i64(state,0,-2,"release")
        return -2
    held:i64=pcc_thread_world_stop_epoch_held()
    result:i64=-3
    if (held>0 and pcc_exp2_exposure_is_revoked()==0 and
        pcc_exp2_window_domain_ok()==1):
        # Not just the central transition/terminal token: mode-only
        # reservations are distributed across 32 shards. During a held epoch
        # their total is coherent; sentinel/overflow or any active reservation
        # precludes opening the scope. The zero-token GC3 exposure hook is
        # independently still required on the other side of this check.
        result=-15
        if pcc_exp2_total_mode_leases()==0:
            result=-4
            if pcc_exp2_install_exposure_hook(function_addr("pcc_exp2_window_expose_cb"))==1:
                result=-5
                if pcc_exp2_process_register_gate(function_addr("pcc_exp2_window_checked_old"),null())==1:
                    result=-15
                    if pcc_thread_world_stop_epoch_held()==held and pcc_exp2_total_mode_leases()==0:
                        result=-6
                        if pcc_exp2_process_enable(8,64)==1:
                            result=1
    current:i64=pcc_thread_world_stop_epoch_held()
    resumed:i64=pcc_resume_world()
    if current!=held or resumed!=0:
        result=-7
    atomic_store_i64(state,0,result,"release")
    return result


@c_abi_export("pcc_exp2_window_stop_minor")
def pcc_exp2_window_stop_minor(epoch:i64)->i64:
    if (epoch<=0 or pcc_exp2_window_active()==0 or pcc_thread_world_stop_epoch_held()!=epoch):
        return -1
    atomic_rmw_i64("add",global_addr("pcc_exp2_window_attempts"),0,1,"relaxed")
    count:i64=pcc_exp2_process_minor_held(epoch)
    if count<0:
        atomic_rmw_i64("add",global_addr("pcc_exp2_window_refused"),0,1,"relaxed")
    return count


@c_abi_export("pcc_exp2_window_metric")
def pcc_exp2_window_metric(i:i64)->i64:
    if i==30:return atomic_load_i64(global_addr("pcc_exp2_window_state"),0,"acquire")
    if i==31:return atomic_load_i64(global_addr("pcc_exp2_window_first_bad_reason"),0,"acquire")
    if i==32:return atomic_load_i64(global_addr("pcc_exp2_window_first_bad_tag"),0,"acquire")
    if i==33:return atomic_load_i64(global_addr("pcc_exp2_window_attempts"),0,"acquire")
    if i==34:return atomic_load_i64(global_addr("pcc_exp2_window_refused"),0,"acquire")
    if i==35:return atomic_load_i64(global_addr("pcc_exp2_window_old_owners"),0,"acquire")
    if i==36:return atomic_load_i64(global_addr("pcc_exp2_window_old_slots"),0,"acquire")
    if i==37:return atomic_load_i32(global_addr("pcc_gc_backend3_closed_young_domain"),0,"acquire")
    if i==38:return pcc_exp2_exposure_is_revoked()
    if i==39:return atomic_load_i64(global_addr("pcc_exp2_image_witness"),0,"acquire")
    if i>=40 and i<=45:return pcc_exp2_old_exact_metric(i-40)
    return pcc_exp2_process_metric(i)
