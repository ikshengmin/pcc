"""Managed-only 48-byte slab ABI, including unregistered raw fallback."""
from pathlib import Path
import pytest
from tests.python.runtime_body_model import load_functions

ALLOCATOR = Path(__file__).resolve().parents[2] / 'pcc/runtime/py/freestanding_allocator.py'


def model(names, **doubles):
    ns = {'i64': int, 'c_ptr': int, **doubles}
    load_functions(ALLOCATOR, set(names), ns, local_helpers=False)
    return ns


def test_object_selector_keeps_raw_abi_and_existing_indices():
    ns = model({'pcc_allocator_size_class', 'pcc_allocator_object_size_class',
                '_object_magazine_class', '_object_magazine_usable', '_granule_stride_count',
                '_object_magazine_push', 'pcc_allocator_thread_cache_drain', '_raw_magazine_take'})
    raw = ns['pcc_allocator_size_class']
    obj = ns['pcc_allocator_object_size_class']
    for n in range(16386):
        assert obj(n) == (48 if 32 < n <= 48 else raw(n))
    assert raw(33) == raw(48) == raw(49) == 64
    for i in range(11):
        size = 16 << i
        assert ns['_object_magazine_class'](size) == i
        assert ns['_object_magazine_usable'](i) == size
    assert ns['_object_magazine_class'](48) == 11
    assert ns['_object_magazine_usable'](11) == 48
    assert ns['_granule_stride_count'](96) == 682
    assert ns['_granule_stride_count'](112) == 585
    assert ns['OBJECT_MAGAZINE_CLASSES'] == 12
    assert ns['OBJECT_MAGAZINE_COUNTS'] >= 12 * 8
    assert ns['RAW_MAGAZINE_BASE'] >= ns['OBJECT_MAGAZINE_COUNTS'] + 12 * 8
    assert ns['RAW_MAGAZINE_BASE'] + ns['OBJECT_MAGAZINE_COUNTS'] + 8*8 <= ns['OBJECT_MAGAZINE_PAGE_BYTES']


@pytest.mark.parametrize('register_ok', [True, False])
def test_refill_managed_48_or_exact_raw64_fallback(register_ok):
    words = {}; raw = []; managed = []; mapped = []; count_calls = []
    base = 0x100000
    ns = model({'pcc_allocator_refill_small_object', '_object_magazine_class'},
        ptr_is_null=lambda p: p == 0, null=lambda: 0,
        ptr_add=lambda p,n:p+n, load_ptr=lambda p,n=0:0,
        global_addr=lambda n:n, page_alloc=lambda n:base,
        atomic_rmw_i64=lambda op,p,o,n,order:mapped.append(n),
        pcc_allocator_initialize_small=lambda p,b,u:words.__setitem__(p,u),
        store_i64=lambda *a:None,
        _span_list_slot=lambda i,w:0,
        _granule_register_slab_locked=lambda b,k,s:(count_calls.append((b,k,s)) or (1 if register_ok else -1)),
        pcc_allocator_put_small=lambda p,u:raw.append((p,u)),
        pcc_allocator_put_small_object=lambda p,u:managed.append((p,u)))
    first = ns['pcc_allocator_refill_small_object'](48)
    assert first == base+48
    assert mapped == [65536]
    assert count_calls == [(base,1,96)]
    usable, stride, count = (48,96,682) if register_ok else (64,112,585)
    entries = managed if register_ok else raw
    assert (raw if register_ok else managed) == []
    expected = [base+48+i*stride for i in range(count)]
    assert entries == [(p,usable) for p in expected[1:]]
    assert all(words[p] == usable for p in expected)
    assert expected[-1] + usable <= base + 65536


@pytest.mark.parametrize('actual_usable', [48,64])
def test_managed48_allocation_charges_actual_carve(actual_usable):
    account = []; published = []; requested = []; chosen = []
    ns = model({'pcc_allocator_alloc_object', 'pcc_allocator_size_class',
                'pcc_allocator_object_size_class'},
        ptr_is_null=lambda p:p==0, null=lambda:0,
        _object_magazine_current=lambda:0,
        pcc_allocator_lock_acquire=lambda:None,
        pcc_allocator_lock_release=lambda:None,
        pcc_allocator_take_small_object=lambda usable:(chosen.append(usable) or 0),
        pcc_allocator_refill_small_object=lambda usable:0x100030,
        atomic_store_i64=lambda *a:published.append(a),
        store_i64=lambda *a:requested.append(a),
        load_i64=lambda ptr,off:actual_usable,
        pcc_allocator_account_allocate=lambda *a:account.append(a))
    assert ns['pcc_allocator_alloc_object'](40) == 0x100030
    assert chosen == [48]
    assert account == [(40,actual_usable,0)]
    assert len(published) == len(requested) == 1


def test_explicit_reverse_mapping_drains_all_object_classes_then_raw():
    calls=[]
    ns=model({'pcc_allocator_thread_cache_drain','_object_magazine_usable'},
        global_addr=lambda n:n,global_load_ptr=lambda n:1234,
        global_store_ptr=lambda *a:None,ptr_is_null=lambda p:p==0,null=lambda:0,
        store_i32=lambda *a:None,pcc_allocator_lock_acquire=lambda:None,
        pcc_allocator_lock_release=lambda:None,
        _object_magazine_drain_locked=lambda *a:calls.append(a),
        _raw_magazine_drain_all_locked=lambda state:calls.append(('raw',state)),
        page_free=lambda state,size:0,atomic_rmw_i64=lambda *a:None)
    ns['pcc_allocator_thread_cache_drain']()
    assert calls==[(1234,i,16<<i,0) for i in range(11)]+[(1234,11,48,0),('raw',1234)]


def test_central_list_and_sparse_queue_capacity_matches_new_index():
    import ast
    tree=ast.parse(ALLOCATOR.read_text())
    arrays={n.value.args[0].value:len(n.value.args)-1 for n in tree.body
            if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call)
            and isinstance(n.value.func,ast.Name) and n.value.func.id=='define_global_i64_array'
            and n.value.args and isinstance(n.value.args[0],ast.Constant)}
    assert arrays['pcc_allocator_object_span_lists']==48
    assert arrays['pcc_allocator_sparse_trim_heads']==12
