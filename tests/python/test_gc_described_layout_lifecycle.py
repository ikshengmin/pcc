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


@pytest.mark.parametrize('inserted',(-1,0,1))
def test_raw_exact_index_and_root_publish_in_one_graph_transaction(inserted):
    from tests.python.runtime_body_model import load_functions
    owner={0:1,8:35,12:0,16:object(),24:1000}
    root={0:None}; events=[]
    def lock(site):
        assert site==2 and root[0] is None
        events.append('lock')
    def insert(obj):
        assert obj is owner and owner[16] is not None and events==['lock']
        events.append('insert')
        return inserted
    def store(base,offset,value):
        assert base is root and offset==0 and value is owner and inserted==1
        assert events==['lock','insert']
        root[offset]=value;events.append('root')
    def unlock():
        assert root[0] is (owner if inserted==1 else None)
        events.append('unlock')
    ns=dict(ptr_is_null=lambda x:int(x is None),is_tagged_int=lambda _:0,
            load_ptr=lambda p,o:p[o],store_ptr=store,
            pcc_py_gc_minor_graph_lock_for=lock,pcc_gc_managed_pointer_index_insert=insert,
            _object_graph_unlock=unlock,
            pcc_gc_reclaim_retired_index_tables_if_alone=lambda:events.append('reclaim'))
    load_functions(RUNTIME/'py_gc_backend.py',{'pcc_gc_pointer_register_raw_into_root'},ns,local_helpers=False)
    assert ns['pcc_gc_pointer_register_raw_into_root'](owner,root)==inserted
    assert events==(['lock','insert','root','unlock','reclaim'] if inserted==1 else ['lock','insert','unlock'])


def test_raw_index_transaction_does_not_replace_a_nonempty_root():
    from tests.python.runtime_body_model import load_functions
    existing=object();owner=object();root={0:existing};events=[]
    ns=dict(ptr_is_null=lambda x:int(x is None),is_tagged_int=lambda _:0,
            load_ptr=lambda p,o:p[o],store_ptr=lambda *_:pytest.fail('published root'),
            pcc_py_gc_minor_graph_lock_for=lambda site:events.append(('lock',site)),
            pcc_gc_managed_pointer_index_insert=lambda _:pytest.fail('inserted key'),
            _object_graph_unlock=lambda:events.append('unlock'),
            pcc_gc_reclaim_retired_index_tables_if_alone=lambda:pytest.fail('unexpected reclaim'))
    load_functions(RUNTIME/'py_gc_backend.py',{'pcc_gc_pointer_register_raw_into_root'},ns,local_helpers=False)
    assert ns['pcc_gc_pointer_register_raw_into_root'](owner,root)==-1
    assert root[0] is existing and events==[('lock',2),'unlock']


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
    if(pcc_gc_described_alloc_into_root(roots,scalar_desc,sizeof(scalar_desc),9223372036854775776LL)||roots[0]) return 2;
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


@pytest.mark.parametrize('registered',(-1,0,1))
def test_raw_constructor_never_holds_no_park_during_large_initialization(registered):
    from tests.python.runtime_body_model import load_functions
    owner={0:0,8:0,12:0,16:None,24:0};root={0:None};descriptor=object();events=[]
    def bind(obj,extent,desc,desc_bytes,repeat):
        assert obj is owner and extent==24048 and desc is descriptor and repeat==1000
        assert events==['zero'];obj[16]=desc;obj[24]=repeat;events.append('bind');return 1
    def probe(obj):
        assert obj is owner and obj[0]==1 and obj[8]==35 and obj[16] is descriptor
        events.append('raw provenance');return 0
    def register(obj,out):
        assert obj is owner and out is root and root[0] is None
        assert events==['zero','bind','raw provenance'];events.append('index transaction')
        if registered==1:root[0]=obj
        return registered
    def freed(obj):
        assert obj is owner and root[0] is None and registered==-1
        events.append('free')
    def abort():
        events.append('abort');raise RuntimeError('fail-stop duplicate')
    ns=dict(PY_TYPE_DESCRIBED_LAYOUT=35,PYOBJECTHEADER_REFCOUNT_OFFSET=0,
            PYOBJECTHEADER_TYPE_TAG_OFFSET=8,PYOBJECTHEADER_FLAGS_OFFSET=12,
            ptr_is_null=lambda v:int(v is None),load_ptr=lambda p,o:p[o],load_i32=lambda *_:0,
            store_ptr=lambda p,o,v:p.__setitem__(o,v),store_i64=lambda p,o,v:p.__setitem__(o,v),
            store_i32=lambda p,o,v:p.__setitem__(o,v),global_addr=lambda n:n,
            null=lambda:None,pcc_gc_described_extent=lambda *_:24048,
            pcc_gc_described_slot_count=lambda *_:2001,pcc_allocator_size_class=lambda _:0,
            pcc_thread_safepoint=lambda:None,pcc_gc_note_alloc=lambda _:None,
            atomic_load_i32=lambda *_:1,pcc_gc_fix_backend=lambda:pytest.fail('already frozen'),
            pcc_allocator_alloc_object=lambda size:owner,
            pcc_thread_no_park_enter=lambda:pytest.fail('raw initialization cannot hold no-park'),
            pcc_thread_no_park_exit=lambda:pytest.fail('raw initialization has no no-park lease'),
            memset=lambda *_:events.append('zero'),pcc_gc_described_initialize_private=bind,
            pcc_allocator_object_publish_fresh=probe,pcc_gc_pointer_register_raw_into_root=register,
            py_gc_track=lambda obj:events.append('track') if obj is root[0] else pytest.fail('not rooted'),
            free=freed,pcc_platform_abort=abort)
    load_functions(RUNTIME/'py_obj.py',{'pcc_gc_described_alloc_into_root'},ns,local_helpers=False)
    f=ns['pcc_gc_described_alloc_into_root']
    if registered==0:
        with pytest.raises(RuntimeError,match='fail-stop duplicate'):f(root,descriptor,64,1000)
        assert events[-1]=='abort' and root[0] is None
    else:
        assert f(root,descriptor,64,1000)==(1 if registered==1 else 0)
        assert events[-1]==('track' if registered==1 else 'free')
        assert root[0] is (owner if registered==1 else None)


RAW_C_SOURCE = r'''
#include "py_runtime.h"
#include "pcc_described_layout.h"
#include <stdint.h>
extern int64_t pcc_gc_granule_is_object_start(void *);
extern int64_t pcc_gc_managed_pointer_index_contains(void *);
extern int64_t pcc_py_gc_managed_pointer_count;
extern int64_t pcc_gc_visit_object_slots_slice(void *,int64_t,int64_t,PccDescribedSlotVisitor,void *,int64_t *);
extern void pcc_gc_store_field_ptr(PyObject *,PyObject **,PyObject *);
extern int64_t pcc_allocator_live_requested_bytes(void);
extern int64_t pcc_gc_backend0_full_begin(void);
extern int64_t pcc_gc_backend0_full_step(void);
extern int64_t pcc_gc_backend0_full_set_slice_us(int64_t);
extern int64_t pcc_gc_backend0_full_set_concurrent(int64_t);
extern int64_t pcc_gc_backend0_full_counter(int64_t);
extern int pcc_gc_full_phase;
extern int pcc_gc_backend0_young_request;
extern int64_t pcc_thread_start(void **,void *(*)(void *),void *);
extern int64_t pcc_thread_join(void *,void **);
static const int64_t scalar[]={1,32,1,0,0};
static const int64_t fixed[]={1,20000,0,2,0,40,19992};
static const int64_t mixed[]={1,48,24,1,2,40,0,16};
static const int64_t bad[]={1,20000,0,1,0,20000};
static int64_t count(PyObject *p){return ((PyObjectHeader *)p)->refcount;}
static PyObject **slot(PyObject *p,int64_t n){return (PyObject **)((char*)p+n);}
static void clear(PyObject **p){pcc_gc_store_root(p,0);}
static int live_raw(PyObject *obj){return pcc_gc_granule_is_object_start(obj)==-1 && pcc_gc_managed_pointer_index_contains(obj)==1;}
static int dead_raw(PyObject *obj){return pcc_gc_managed_pointer_index_contains(obj)==0;}
static int64_t visited,last,bad_role;
static void observe(void *p,int64_t role,void *context){
    if(role!=1)bad_role=1;
    visited++;last=(char*)p-(char*)context;
}
static int64_t ready,go,finished;
static void *worker(void *arg){
    PyObject *child=(PyObject*)arg,*owner=0;
    if(pcc_current_thread_id()<=0)return (void*)1;
    void *h=pcc_gc_scheduler_root_register_handle(&owner);if(!h)return (void*)2;
    __atomic_store_n(&ready,1,__ATOMIC_RELEASE);
    while(!__atomic_load_n(&go,__ATOMIC_ACQUIRE))pcc_thread_safepoint();
    for(int i=0;i<100;i++){
        if(!pcc_gc_described_alloc_into_root(&owner,mixed,sizeof(mixed),1000))return (void*)3;
        if(!live_raw(owner)||count(owner)!=1||!py_gc_is_tracked(owner)||*slot(owner,24040))return (void*)4;
        pcc_gc_store_field_ptr(owner,slot(owner,24040),child);
        clear(&owner);if(owner)return (void*)5;
        pcc_thread_safepoint();
    }
    pcc_gc_scheduler_root_unregister_handle(h);
    __atomic_store_n(&finished,1,__ATOMIC_RELEASE);
    return 0;
}
int main(void){
    py_gc_init();py_gc_disable();
    PyObject *roots[3]={0};void *handles[3];int64_t registered=pcc_gc_scheduler_root_count();
    for(int i=0;i<3;i++){handles[i]=pcc_gc_scheduler_root_register_handle(roots+i);if(!handles[i])return 1;}
    if(pcc_gc_scheduler_root_count()!=registered+3)return 2;
    int64_t keys=pcc_py_gc_managed_pointer_count;
    if(pcc_gc_described_alloc_into_root(roots,bad,sizeof(bad),0)||roots[0])return 3;
    if(pcc_gc_described_alloc_into_root(roots,scalar,sizeof(scalar),9223372036854775776LL)||roots[0])return 4;
    /* Geometry fits signed i64, but allocator mapping overhead would overflow. */
    if(pcc_gc_described_alloc_into_root(roots,scalar,sizeof(scalar),9223372036854775775LL)||roots[0])return 5;
    if(pcc_py_gc_managed_pointer_count!=keys)return 6;
    /* Warm the exact-index/allocator path before asserting requested bytes. */
    if(!pcc_gc_described_alloc_into_root(roots,scalar,sizeof(scalar),20000))return 7;
    PyObject *p=roots[0];if(!live_raw(p)||py_gc_is_tracked(p)||count(p)!=1)return 8;
    clear(roots);if(!dead_raw(p)||pcc_py_gc_managed_pointer_count!=keys)return 9;
    int64_t bytes=pcc_allocator_live_requested_bytes();
    if(!pcc_gc_described_alloc_into_root(roots,scalar,sizeof(scalar),20000))return 10;
    p=roots[0];if(pcc_allocator_live_requested_bytes()!=bytes+20032||pcc_py_gc_managed_pointer_count!=keys+1)return 11;
    ((char*)p)[20031]=17;
    int64_t state[2]={0};if(!pcc_gc_visit_object_slots_slice(p,0,1,observe,p,state)||state[0]!=-1||state[1]||visited)return 12;
    clear(roots);if(!dead_raw(p)||pcc_allocator_live_requested_bytes()!=bytes||pcc_py_gc_managed_pointer_count!=keys)return 13;
    /* Raw fixed and repeated backing share the same precise slot owner. */
    if(!pcc_gc_described_alloc_into_root(roots,fixed,sizeof(fixed),0))return 14;
    if(!pcc_gc_described_alloc_into_root(roots+1,mixed,sizeof(mixed),1000))return 15;
    if(!pcc_gc_described_alloc_into_root(roots+2,scalar,sizeof(scalar),8))return 16;
    PyObject *a=roots[0],*b=roots[1],*small=roots[2];
    if(!live_raw(a)||!live_raw(b)||pcc_gc_granule_is_object_start(small)!=1||pcc_py_gc_managed_pointer_count!=keys+2)return 17;
    if(!py_gc_is_tracked(a)||!py_gc_is_tracked(b))return 18;
    visited=last=0;
    if(!pcc_gc_visit_object_slots_slice(b,1998,2,observe,b,state)||state[0]!=2000||state[1]!=2||last!=24024||visited!=2)return 19;
    if(!pcc_gc_visit_object_slots_slice(b,state[0],2,observe,b,state)||state[0]!=-1||state[1]!=1||last!=24040||visited!=3||bad_role)return 20;
    *(int64_t*)((char*)a+32)=(int64_t)(void*)small;
    *(int64_t*)((char*)b+56)=(int64_t)(void*)small;
    pcc_gc_store_field_ptr(a,slot(a,40),b);pcc_gc_store_field_ptr(a,slot(a,19992),b);
    pcc_gc_store_field_ptr(b,slot(b,40),a);pcc_gc_store_field_ptr(b,slot(b,24040),a);
    pcc_gc_store_field_ptr(b,slot(b,48),small);
    if(count(a)!=3||count(b)!=3||count(small)!=2)return 21;
    clear(roots+2);if(count(small)!=1)return 22;
    if(py_gc_collect()!=0||!live_raw(a)||!live_raw(b))return 23;
    clear(roots);clear(roots+1);
    if(count(a)!=2||count(b)!=2||py_gc_collect()!=2||!dead_raw(a)||!dead_raw(b)||pcc_gc_granule_is_object_start(small)!=-1||pcc_py_gc_managed_pointer_count!=keys)return 24;
    if(!pcc_gc_described_alloc_into_root(roots,fixed,sizeof(fixed),0))return 25;
    a=roots[0];pcc_gc_store_field_ptr(a,slot(a,19992),a);clear(roots);
    pcc_gc_backend0_young_request=1;
    if(py_gc_collect()!=1||!dead_raw(a)||pcc_py_gc_managed_pointer_count!=keys)return 26;
    pcc_gc_backend0_young_request=0;
    /* Allocate large owners on a real carrier while another collects. */
    if(!pcc_gc_described_alloc_into_root(roots,scalar,sizeof(scalar),8))return 27;
    small=roots[0];void *thread=0,*status=0;
    if(pcc_thread_start(&thread,worker,small)!=0)return 28;
    while(!__atomic_load_n(&ready,__ATOMIC_ACQUIRE))pcc_thread_safepoint();
    __atomic_store_n(&go,1,__ATOMIC_RELEASE);
    for(int i=0;i<100&&!__atomic_load_n(&finished,__ATOMIC_ACQUIRE);i++){py_gc_collect();pcc_thread_safepoint();}
    if(pcc_thread_join(thread,&status)!=0||status||count(small)!=1||pcc_py_gc_managed_pointer_count!=keys)return 29;
    clear(roots);
    /* Full bounded mark sees exact-index owners, then young handover reclaims. */
    if(!pcc_gc_described_alloc_into_root(roots,mixed,sizeof(mixed),1000))return 30;
    a=roots[0];pcc_gc_store_field_ptr(a,slot(a,24040),a);
    if(py_gc_collect()!=0)return 31;clear(roots);
    py_gc_enable();pcc_gc_backend0_full_set_slice_us(0);pcc_gc_backend0_full_set_concurrent(0);
    int64_t cycles=pcc_gc_backend0_full_counter(0),stops=pcc_gc_backend0_full_counter(1);
    if(!pcc_gc_backend0_full_begin())return 32;
    for(int i=0;i<20000&&pcc_gc_full_phase;i++)pcc_gc_backend0_full_step();
    if(pcc_gc_full_phase||pcc_gc_backend0_full_counter(0)!=cycles+1||pcc_gc_backend0_full_counter(1)<=stops+1)return 33;
    pcc_gc_backend0_young_request=1;
    if(py_gc_collect()!=1||!dead_raw(a)||pcc_py_gc_managed_pointer_count!=keys)return 34;
    pcc_gc_backend0_young_request=0;
    for(int i=0;i<3;i++)pcc_gc_scheduler_root_unregister_handle(handles[i]);
    if(pcc_gc_scheduler_root_count()!=registered)return 35;
    return 42;
}
'''


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if sys.platform == 'linux' and platform.machine().lower() in ('x86_64','amd64') else 'requires native Linux x86-64')
def test_described_raw_mapping_lifecycle_native(tmp_path,pcc_runtime_archive):
    from tests.owned_runtime_c_fixture import link_c_harness_bounded
    source=tmp_path/'described-raw.c';exe=tmp_path/'described-raw'
    source.write_text(RAW_C_SOURCE)
    result=link_c_harness_bounded(source,exe,runtime_archive=pcc_runtime_archive)
    assert result.returncode==0,(result.stdout,result.stderr)
    run=subprocess.run([str(exe)],capture_output=True,timeout=30)
    assert (run.returncode,run.stdout,run.stderr)==(42,b'',b''),(run.returncode,run.stdout,run.stderr)


@pytest.mark.integration
@pytest.mark.parametrize('target',('aarch64-unknown-linux-gnu','arm64-apple-darwin','x86_64-pc-windows-msvc'))
def test_final_described_lifecycle_owned_cross_target_object_abi(tmp_path,monkeypatch,target):
    """Actual self object and C caller ABI, never cross-target execution."""
    import re
    from pcc.frontends.python import owned_runtime_build
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.ir.optimization.driver import optimize_ir
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from tests.owned_runtime_c_fixture import object_symbols

    runtime=RUNTIME.parent
    modules={
        'py_obj': {'pcc_gc_described_alloc_into_root':('i64','ptr,ptr,i64,i64')},
        'py_gc_backend': {'pcc_gc_pointer_register_raw_into_root':('i64','ptr,ptr')},
        'py_obj_dealloc': {'py_dealloc_described_layout':('void','ptr')},
        'freestanding_gc_backend0_collector': {'pcc_gc_backend0_dealloc_unreachable':('void','ptr')},
        'freestanding_gc_object_slots': {'pcc_gc_visit_object_slots_slice':('i64','ptr,i64,i64,ptr,ptr,ptr')},
        'freestanding_gc_described_layout': {
            'pcc_gc_described_extent':('i64','ptr,i64,i64'),
            'pcc_gc_described_slot_count':('i64','ptr,i64,i64'),
            'pcc_gc_described_slot_offset':('i64','ptr,i64,i64,i64'),
            'pcc_gc_described_initialize_private':('i64','ptr,i64,ptr,i64,i64'),
            'pcc_gc_described_visit_slots_slice':('i64','ptr,i64,i64,ptr,ptr,ptr'),
        },
    }
    assert set(modules)<=set(owned_runtime_build.runtime_modules(str(runtime),target))
    definitions={};undefined={}
    def symbols(path):
        if 'windows' in target:
            from pcc.backend.coff_x86_64 import parse_object
            rows=parse_object(path.read_bytes()).symbols
            return ({s.name for s in rows if s.name and s.section>0},
                    {s.name for s in rows if s.name and s.section==0})
        return ({line.split()[-1].lstrip('_') for line in object_symbols(path).stdout.splitlines()},
                {line.split()[-1].lstrip('_') for line in object_symbols(path,undefined=True).stdout.splitlines()})
    with monkeypatch.context() as guard:
        guard.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('external target tool invoked'))
        for module,expected in modules.items():
            ir=tmp_path/(module+'.ll');obj=tmp_path/(module+'.o')
            owned_runtime_build._compile_runtime_module(module,str(RUNTIME/(module+'.py')),str(ir),target)
            text=ir.read_text()
            for symbol,(result,parameters) in expected.items():
                definition=re.search(r'define external '+result+r' @'+symbol+r'\(([^)]*)\)',text)
                assert definition, (target,module,symbol)
                assert ','.join(p.strip().split()[0] for p in definition.group(1).split(','))==parameters
            obj.write_bytes(emit_owned_object(optimize_ir(text,owned_runtime_build.runtime_ir_passes(str(runtime))),target))
            definitions[module],undefined[module]=symbols(obj)
            assert set(expected)<=definitions[module]
        # C ABI declarations use int64_t on all supported 64-bit data models
        # (Windows LLP64 included), not the host's long width or a fake header.
        code='''#include "pcc_described_layout.h"
int64_t probe(void *out,const int64_t *d,int64_t n,int64_t count) {
    return pcc_gc_described_alloc_into_root(out,d,n,count);
}
int64_t walk(void *owner,int64_t cursor,PccDescribedSlotVisitor callback,void *context,int64_t *state) {
    return pcc_gc_described_visit_slots_slice(owner,cursor,3,callback,context,state);
}'''
        units=CEvaluator(backend='self',target_triple=target).compile_translation_units(
            [TranslationUnit('cross.c',None,code)],use_system_cpp=False,
            include_dirs=[str(runtime/'include')],frontend_opt_level=0,use_compile_cache=False)
        caller=units[0][1];obj=tmp_path/'caller.o';obj.write_bytes(emit_owned_object(caller,target))
        _defined,caller_undef=symbols(obj)
    assert {'pcc_gc_described_alloc_into_root','pcc_gc_described_visit_slots_slice'}<=caller_undef
    assert re.search(r'declare i64 @"?pcc_gc_described_alloc_into_root"?\(ptr[^,]*, ptr[^,]*, i64[^,]*, i64[^)]*\)',caller)
    assert re.search(r'declare i64 @"?pcc_gc_described_visit_slots_slice"?\(ptr[^,]*, i64[^,]*, i64[^,]*, ptr[^,]*, ptr[^,]*, ptr[^)]*\)',caller)
    # Verify the actual cross-object relocation obligations resolve to exactly
    # one of the tested production owners, including raw publication and sweep.
    for consumer,symbol,owner in (
        ('py_obj','pcc_gc_pointer_register_raw_into_root','py_gc_backend'),
        ('py_obj','pcc_gc_described_initialize_private','freestanding_gc_described_layout'),
        ('py_obj','pcc_gc_described_extent','freestanding_gc_described_layout'),
        ('py_obj_dealloc','pcc_gc_visit_object_slots','freestanding_gc_object_slots'),
        ('freestanding_gc_backend0_collector','py_dealloc_described_layout','py_obj_dealloc'),
        ('freestanding_gc_object_slots','pcc_gc_described_visit_slots_slice','freestanding_gc_described_layout'),
    ):
        assert symbol in undefined[consumer],(target,consumer,symbol)
        assert {m for m,names in definitions.items() if symbol in names}=={owner}
