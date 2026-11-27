"""Independent managed-only 80-byte class, preserving existing raw/48 ABI."""
from pathlib import Path
import pytest
from tests.python.runtime_body_model import load_functions

ALLOCATOR=Path(__file__).resolve().parents[2]/'pcc/runtime/py/freestanding_allocator.py'


def model(names, **doubles):
    ns={'i64':int,'c_ptr':int,**doubles}
    load_functions(ALLOCATOR,set(names),ns,local_helpers=False)
    return ns


def test_managed80_selector_and_geometry_preserve_all_preexisting_indices():
    ns=model({'pcc_allocator_size_class','pcc_allocator_object_size_class','_object_magazine_class','_object_magazine_usable','_granule_stride_count','_object_magazine_push','pcc_allocator_thread_cache_drain','_raw_magazine_take'})
    raw=ns['pcc_allocator_size_class'];obj=ns['pcc_allocator_object_size_class']
    for n in range(16386):
        assert obj(n)==(48 if 32<n<=48 else 80 if 64<n<=80 else raw(n))
    assert raw(65)==raw(80)==raw(81)==128
    for i in range(11):
        assert ns['_object_magazine_class'](16<<i)==i
        assert ns['_object_magazine_usable'](i)==16<<i
    assert ns['_object_magazine_class'](48)==11 and ns['_object_magazine_usable'](11)==48
    assert ns['_object_magazine_class'](80)==12 and ns['_object_magazine_usable'](12)==80
    assert ns['_granule_stride_count'](128)==512
    assert ns['_granule_stride_count'](96)==682 and ns['_granule_stride_count'](176)==372
    assert ns['OBJECT_MAGAZINE_CLASSES']==13
    assert ns['OBJECT_MAGAZINE_COUNTS']>=13*8
    assert ns['RAW_MAGAZINE_BASE']>=ns['OBJECT_MAGAZINE_COUNTS']+13*8
    assert ns['RAW_MAGAZINE_BASE']+ns['OBJECT_MAGAZINE_COUNTS']+8*8<=ns['OBJECT_MAGAZINE_PAGE_BYTES']


@pytest.mark.parametrize('register_ok',[True,False])
def test_refill_managed80_or_exact_raw128_fallback(register_ok):
    words={};raw=[];managed=[];mapped=[];calls=[];base=0x100000
    ns=model({'pcc_allocator_refill_small_object','_object_magazine_class'},
        ptr_is_null=lambda p:p==0,null=lambda:0,ptr_add=lambda p,n:p+n,
        load_ptr=lambda p,n=0:0,global_addr=lambda n:n,page_alloc=lambda n:base,
        atomic_rmw_i64=lambda op,p,o,n,order:mapped.append(n),
        pcc_allocator_initialize_small=lambda p,b,u:words.__setitem__(p,u),
        store_i64=lambda *a:None,_span_list_slot=lambda i,w:0,
        _granule_register_slab_locked=lambda b,k,s:(calls.append((b,k,s)) or (1 if register_ok else -1)),
        pcc_allocator_put_small=lambda p,u:raw.append((p,u)),pcc_allocator_put_small_object=lambda p,u:managed.append((p,u)))
    assert ns['pcc_allocator_refill_small_object'](80)==base+48
    assert mapped==[65536] and calls==[(base,1,128)]
    usable,stride,count=(80,128,512) if register_ok else (128,176,372)
    entries=managed if register_ok else raw
    assert (raw if register_ok else managed)==[]
    expected=[base+48+i*stride for i in range(count)]
    assert entries==[(p,usable) for p in expected[1:]]
    assert all(words[p]==usable for p in expected)
    assert expected[-1]+usable<=base+65536


@pytest.mark.parametrize('actual_usable',[80,128])
def test_managed80_alloc_charges_actual_carve(actual_usable):
    acc=[];chosen=[];pub=[];requested=[]
    ns=model({'pcc_allocator_alloc_object','pcc_allocator_size_class','pcc_allocator_object_size_class'},
       ptr_is_null=lambda p:p==0,null=lambda:0,_object_magazine_current=lambda:0,
       pcc_allocator_lock_acquire=lambda:None,pcc_allocator_lock_release=lambda:None,
       pcc_allocator_take_small_object=lambda usable:(chosen.append(usable) or 0),
       pcc_allocator_refill_small_object=lambda usable:0x100030,
       atomic_store_i64=lambda *a:pub.append(a),store_i64=lambda *a:requested.append(a),
       load_i64=lambda p,o:actual_usable,pcc_allocator_account_allocate=lambda *a:acc.append(a))
    assert ns['pcc_allocator_alloc_object'](72)==0x100030
    assert chosen==[80] and acc==[(72,actual_usable,0)] and len(pub)==len(requested)==1
