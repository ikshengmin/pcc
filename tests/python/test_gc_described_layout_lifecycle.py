"""#13 described-object lifecycle: exact publication and real collector controls."""
from pathlib import Path
import ast
import os
import platform
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / 'pcc/runtime/py'


def test_described_allocation_publication_and_root_handoff_order():
    from tests.python.runtime_body_model import load_functions
    obj = {0:0,8:0,12:0,16:None,24:0}
    out = {0:None}
    descriptor = object()
    events=[]
    def init(owner, extent, desc, desc_bytes, repeat):
        assert owner is obj and extent==48 and desc is descriptor and repeat==2
        assert events[-1]=='zero'
        owner[16]=desc;owner[24]=repeat
        events.append('descriptor')
        return 1
    def publish(owner):
        assert owner[0]==1 and owner[8]==35 and owner[12]==0
        assert owner[16] is descriptor and owner[24]==2
        assert out[0] is None and events[-1]=='descriptor'
        events.append('live')
        return 1
    def store(base, off, value):
        if base is out:
            assert events[-1]=='live' and value is obj
            events.append('root')
        base[off]=value
    def exit_no_park():
        assert out[0] is obj and events[-1]=='root'
        events.append('park allowed')
    def track(owner):
        assert out[0] is obj and owner is obj and events[-1]=='park allowed'
        events.append('track')
    ns=dict(PY_TYPE_DESCRIBED_LAYOUT=35,PYOBJECTHEADER_REFCOUNT_OFFSET=0,
            PYOBJECTHEADER_TYPE_TAG_OFFSET=8,PYOBJECTHEADER_FLAGS_OFFSET=12,
            ptr_is_null=lambda v:int(v is None),load_ptr=lambda p,o:p[o],load_i32=lambda *_:0,
            store_ptr=store,store_i64=store,store_i32=store,global_addr=lambda n:n,
            null=lambda:None,pcc_gc_described_extent=lambda *_:48,
            pcc_gc_described_slot_count=lambda *_:2,pcc_allocator_size_class=lambda _:64,
            pcc_thread_safepoint=lambda:None,pcc_gc_note_alloc=lambda _:None,
            atomic_load_i32=lambda *_:1,pcc_gc_fix_backend=lambda:pytest.fail('already frozen'),
            pcc_allocator_alloc_object=lambda size:obj,
            pcc_thread_no_park_enter=lambda:events.append('no park'),
            memset=lambda *_:events.append('zero'),pcc_gc_described_initialize_private=init,
            pcc_allocator_object_publish_fresh=publish,pcc_thread_no_park_exit=exit_no_park,
            py_gc_track=track,free=lambda _:pytest.fail('unexpected free'),
            pcc_platform_abort=lambda:pytest.fail('unexpected abort'))
    load_functions(RUNTIME/'py_obj.py', {'pcc_gc_described_alloc_into_root'}, ns, local_helpers=False)
    assert ns['pcc_gc_described_alloc_into_root'](out,descriptor,48,2)==1
    assert events==['no park','zero','descriptor','live','root','park allowed','track']


C_SOURCE = r'''
#include "py_runtime.h"
#include "pcc_described_layout.h"
#include <stdint.h>
extern int64_t pcc_gc_granule_is_object_start(void *);
extern int64_t pcc_gc_visit_object_slots_slice(void *,int64_t,int64_t,PccDescribedSlotVisitor,void *,int64_t *);
extern void pcc_gc_store_field_ptr(PyObject *,PyObject **,PyObject *);
extern int64_t pcc_thread_start(void **,void *(*)(void *),void *);
extern int64_t pcc_thread_join(void *,void **);
extern int64_t pcc_gc_backend0_full_set_slice_us(int64_t);
extern int64_t pcc_gc_backend0_full_set_concurrent(int64_t);
extern int64_t pcc_gc_backend0_full_counter(int64_t);
extern int64_t pcc_gc_backend0_full_begin(void);
extern int64_t pcc_gc_backend0_full_step(void);
extern int pcc_gc_full_phase;
extern int pcc_gc_backend0_young_request;
extern int64_t pcc_allocator_live_requested_bytes(void);
static const int64_t scalar_desc[]={1,32,1,0,0};
static const int64_t fixed_desc[]={1,48,0,1,0,40};
static const int64_t mixed_desc[]={1,48,24,1,2,40,0,16};
static const int64_t bad_desc[]={1,48,0,1,0,16};
static int seen;
static void observe(void *slot,int64_t role,void *ctx) {
    if(role!=1 || !slot || !ctx) seen=-100;
    else seen++;
}
static int64_t count(PyObject *p){return ((PyObjectHeader *)p)->refcount;}
static PyObject **slot(PyObject *p,int64_t n){return (PyObject **)((char *)p+n);}
static void clear(PyObject **p){pcc_gc_store_root(p,0);}
typedef struct Shared {PyObject *owner;PyObject *child;} Shared;
static int64_t ready,go;
static void *mutate(void *arg) {
    Shared *s=(Shared *)arg;
    if(pcc_current_thread_id()<=0)return (void*)1;
    __atomic_store_n(&ready,1,__ATOMIC_RELEASE);
    while(!__atomic_load_n(&go,__ATOMIC_ACQUIRE))pcc_thread_safepoint();
    for(int i=0;i<500;i++){
        pcc_gc_store_field_ptr(s->owner,slot(s->owner,40),s->child);
        pcc_gc_store_field_ptr(s->owner,slot(s->owner,40),0);
        pcc_thread_safepoint();
    }
    PyObject *owned=0;
    void *handle=pcc_gc_scheduler_root_register_handle(&owned);
    if(!handle)return (void*)2;
    for(int i=0;i<100;i++){
        if(!pcc_gc_described_alloc_into_root(&owned,fixed_desc,sizeof(fixed_desc),0))return (void*)3;
        if(count(owned)!=1||!py_gc_is_tracked(owned)||*slot(owned,40))return (void*)4;
        pcc_gc_store_field_ptr(owned,slot(owned,40),s->child);
        clear(&owned);
        if(owned)return (void*)5;
        pcc_thread_safepoint();
    }
    pcc_gc_scheduler_root_unregister_handle(handle);
    return 0;
}
int main(void) {
    py_gc_init();py_gc_disable();
    PyObject *roots[3]={0};
    int64_t registered=pcc_gc_scheduler_root_count();
    void *handles[3];
    for(int i=0;i<3;i++){
        handles[i]=pcc_gc_scheduler_root_register_handle(roots+i);
        if(!handles[i])return 33;
    }
    if(pcc_gc_scheduler_root_count()!=registered+3)return 34;
    if(pcc_gc_described_alloc_into_root(roots,bad_desc,sizeof(bad_desc),0)||roots[0]) return 1;
    if(pcc_gc_described_alloc_into_root(roots,scalar_desc,sizeof(scalar_desc),16384)||roots[0]) return 2;
    if(pcc_gc_alloc(48,PY_TYPE_DESCRIBED_LAYOUT,0)) return 3;
    if(!pcc_gc_described_alloc_into_root(roots,scalar_desc,sizeof(scalar_desc),0)) return 4;
    PyObject *scalar=roots[0];
    if(count(scalar)!=1 || pcc_gc_granule_is_object_start(scalar)!=1 || py_gc_is_tracked(scalar))return 5;
    if(pcc_gc_described_alloc_into_root(roots,scalar_desc,sizeof(scalar_desc),5)||roots[0]!=scalar)return 6;
    int64_t state[2]={0};seen=0;
    if(!pcc_gc_visit_object_slots_slice(scalar,0,1,observe,scalar,state)||state[0]!=-1||state[1]!=0||seen)return 7;
    clear(roots); if(pcc_gc_granule_is_object_start(scalar)!=-1)return 8;
    int64_t bytes_before=pcc_allocator_live_requested_bytes();
    if(!pcc_gc_described_alloc_into_root(roots,scalar_desc,sizeof(scalar_desc),73))return 36;
    if(pcc_allocator_live_requested_bytes()!=bytes_before+105)return 37;
    clear(roots);if(pcc_allocator_live_requested_bytes()!=bytes_before)return 38;
    if(!pcc_gc_described_alloc_into_root(roots,mixed_desc,sizeof(mixed_desc),3))return 9;
    if(!pcc_gc_described_alloc_into_root(roots+1,fixed_desc,sizeof(fixed_desc),0))return 10;
    PyObject *a=roots[0],*b=roots[1];
    if(count(a)!=1||count(b)!=1||!py_gc_is_tracked(a)||!py_gc_is_tracked(b))return 11;
    *(int64_t *)((char*)a+32)=(int64_t)(void*)b; /* scalar pointer-shaped bits, not an edge */
    pcc_gc_store_field_ptr(a,slot(a,40),b); pcc_gc_store_field_ptr(b,slot(b,40),a);
    if(count(a)!=2||count(b)!=2)return 12;
    seen=0;if(!pcc_gc_visit_object_slots_slice(a,0,2,observe,a,state)||seen!=2||state[0]!=2||state[1]!=2)return 13;
    if(py_gc_collect()!=0||pcc_gc_granule_is_object_start(a)!=1||pcc_gc_granule_is_object_start(b)!=1)return 14;
    clear(roots);clear(roots+1);
    if(count(a)!=1||count(b)!=1||py_gc_collect()!=2||pcc_gc_granule_is_object_start(a)!=-1||pcc_gc_granule_is_object_start(b)!=-1)return 15;
    /* Young reclamation uses the actual generation-aware materialization. */
    if(!pcc_gc_described_alloc_into_root(roots,fixed_desc,sizeof(fixed_desc),0))return 16;
    a=roots[0];pcc_gc_store_field_ptr(a,slot(a,40),a);clear(roots);
    pcc_gc_backend0_young_request=1;
    if(py_gc_collect()!=1||pcc_gc_granule_is_object_start(a)!=-1)return 17;
    pcc_gc_backend0_young_request=0;
    /* Acyclic terminal decref releases the exact child and never the scalar. */
    if(!pcc_gc_described_alloc_into_root(roots,fixed_desc,sizeof(fixed_desc),0))return 18;
    if(!pcc_gc_described_alloc_into_root(roots+1,scalar_desc,sizeof(scalar_desc),8))return 19;
    a=roots[0];b=roots[1];
    *(int64_t *)((char*)a+32)=(int64_t)(void*)b;
    pcc_gc_store_field_ptr(a,slot(a,40),b);clear(roots+1);
    if(count(b)!=1)return 20;
    clear(roots);if(pcc_gc_granule_is_object_start(a)!=-1||pcc_gc_granule_is_object_start(b)!=-1)return 21;
    /* Same field mutates from two registered carriers during a collector stop. */
    if(!pcc_gc_described_alloc_into_root(roots,fixed_desc,sizeof(fixed_desc),0))return 22;
    if(!pcc_gc_described_alloc_into_root(roots+1,scalar_desc,sizeof(scalar_desc),8))return 23;
    a=roots[0];b=roots[1];Shared shared;shared.owner=a;shared.child=b;
    void *thread=0,*status=0;
    if(pcc_thread_start(&thread,mutate,&shared)!=0)return 24;
    while(!__atomic_load_n(&ready,__ATOMIC_ACQUIRE))pcc_thread_safepoint();
    __atomic_store_n(&go,1,__ATOMIC_RELEASE);
    for(int i=0;i<500;i++){
        pcc_gc_store_field_ptr(a,slot(a,40),b);
        pcc_gc_store_field_ptr(a,slot(a,40),0);
        if(i%50==0)py_gc_collect();
    }
    if(pcc_thread_join(thread,&status)!=0||status)return 25;
    pcc_gc_store_field_ptr(a,slot(a,40),0);
    if(count(a)!=1||count(b)!=1)return 26;
    clear(roots);clear(roots+1);
    if(pcc_gc_granule_is_object_start(a)!=-1||pcc_gc_granule_is_object_start(b)!=-1)return 27;
    /* Age a cycle, run the actual bounded full mark in small stops, then
       reclaim its handed-over candidates using the young collector. */
    if(!pcc_gc_described_alloc_into_root(roots,fixed_desc,sizeof(fixed_desc),0))return 28;
    a=roots[0];pcc_gc_store_field_ptr(a,slot(a,40),a);
    if(py_gc_collect()!=0)return 29;
    clear(roots);
    py_gc_enable();
    pcc_gc_backend0_full_set_slice_us(0);pcc_gc_backend0_full_set_concurrent(0);
    int64_t completed=pcc_gc_backend0_full_counter(0),stops=pcc_gc_backend0_full_counter(1);
    if(!pcc_gc_backend0_full_begin())return 30;
    for(int i=0;i<10000&&pcc_gc_full_phase;i++)pcc_gc_backend0_full_step();
    if(pcc_gc_full_phase||pcc_gc_backend0_full_counter(0)!=completed+1||pcc_gc_backend0_full_counter(1)<=stops+1)return 31;
    pcc_gc_backend0_young_request=1;
    if(py_gc_collect()!=1||pcc_gc_granule_is_object_start(a)!=-1)return 32;
    pcc_gc_backend0_young_request=0;
    for(int i=0;i<3;i++)pcc_gc_scheduler_root_unregister_handle(handles[i]);
    if(pcc_gc_scheduler_root_count()!=registered)return 35;
    return 42;
}
'''


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if sys.platform == 'linux' and platform.machine().lower() in ('x86_64','amd64') else 'requires native Linux x86-64')
def test_described_lifecycle_native(tmp_path,pcc_runtime_archive):
    from tests.owned_runtime_c_fixture import link_c_harness_bounded
    source=tmp_path/'described.c';exe=tmp_path/'described'
    source.write_text(C_SOURCE)
    result=link_c_harness_bounded(source,exe,runtime_archive=pcc_runtime_archive)
    assert result.returncode==0,(result.stdout,result.stderr)
    run=subprocess.run([str(exe)],capture_output=True,timeout=30)
    assert (run.returncode,run.stdout,run.stderr)==(42,b'',b''),(run.returncode,run.stdout,run.stderr)
