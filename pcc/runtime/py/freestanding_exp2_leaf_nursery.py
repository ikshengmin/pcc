"""Experimental single-owner slab nursery kernel for pointer-free objects.

Descriptor, aligned payload, descriptors and remembered slots belong to the
caller. Allocation is single-carrier; collection requires exclusive ownership
and a complete root cut. Nothing in the routine treats an arbitrary word as
a pointer to dereference. Only strong slots with storage stable through the
cut may be passed to remember(); unadmitted domains must retain stock code.
Only whole, empty-of-roots pages are recycled. A page containing any survivor
is promoted in-place (conservative roots and object addresses never move).
"""
from pcc import i64
from pcc.extern import c_abi_export,c_ptr, c_int64, extern
from pcc.unsafe import (
    abi_constant, atomic_cas_i64, atomic_load_i32, atomic_load_i64,
    atomic_rmw_i64, atomic_store_i64,
    call_i64_i64_ptr, calloc, clock_gettime, define_global_i64, define_global_ptr_null,
    define_thread_local_ptr_null, free, function_addr, global_addr,
    global_load_ptr, global_store_ptr, int_to_ptr, load_i64, load_ptr, memset,
    null, ptr_add, ptr_diff, ptr_is_null, ptr_to_int, stack_alloc, store_i32,
    store_i64, store_ptr,
)
__pcc_freestanding__ = True
PAGE_BYTES = 4096
CELL_BYTES = 64
META_BYTES = 16
EMPTY = 0
YOUNG = 1
OLD = 2
SEEN_YOUNG = 3
EXPOSED = 4     # irrevocable, stops bumping immediately; the next cut seals it
RECYCLING = 5   # not a live page, protects exposure against an overlapping cut

# Nursery descriptor fields (all int64 or pointer).
BASE=0
META=8
PAGE_COUNT=16
REMEMBERED=24
REM_CAP=32
REM_COUNT=40
ACTIVE=48
UNSAFE_CUT=56
ALLOCATIONS=64
MINORS=72
SLOTS_VISITED=80
PAGES_PROMOTED=88
PAGES_RECYCLED=96
# For a fixed arena the refill cursor scans each dense run only once.
NEXT_REFILL=104
REQUESTED_BYTES=112
PROMOTED_OCCUPIED_CELLS=120
OWNER=128
STRIDE=136
STATE_BYTES=144


@c_abi_export("pcc_exp2_leaf_init")
def pcc_exp2_leaf_init(s, base, meta, count: i64, rem, cap: i64) -> i64:
    return pcc_exp2_leaf_init_sized(s,base,meta,count,rem,cap,64)


@c_abi_export("pcc_exp2_leaf_init_sized")
def pcc_exp2_leaf_init_sized(s, base, meta, count: i64, rem, cap: i64, stride: i64) -> i64:
    if (ptr_is_null(s) or ptr_is_null(base) or ptr_is_null(meta) or ptr_is_null(rem)
            or count <= 0 or count > 1048576 or cap < 1 or cap > 4194304):
        return 0
    # Segregated exact leaves: no per-cell header/index is used for size or
    # provenance, and a pointer may still denote any interior address.
    if stride!=32 and stride!=64 and stride!=128:
        return 0
    store_i64(s,STRIDE,stride)
    store_ptr(s,BASE,base)
    store_ptr(s,META,meta)
    store_i64(s,PAGE_COUNT,count)
    store_ptr(s,REMEMBERED,rem)
    store_i64(s,REM_CAP,cap)
    store_i64(s,REM_COUNT,0)
    store_i64(s,ACTIVE,-1)
    store_i64(s,UNSAFE_CUT,0)
    store_i64(s,ALLOCATIONS,0)
    store_i64(s,MINORS,0)
    store_i64(s,SLOTS_VISITED,0)
    store_i64(s,PAGES_PROMOTED,0)
    store_i64(s,PAGES_RECYCLED,0)
    store_i64(s,NEXT_REFILL,0)
    store_i64(s,REQUESTED_BYTES,0)
    store_i64(s,PROMOTED_OCCUPIED_CELLS,0)
    atomic_store_i64(s,OWNER,0,"release")
    i: i64=0
    while i<count:
        atomic_store_i64(meta,i*META_BYTES,EMPTY,"release")
        atomic_store_i64(meta,i*META_BYTES+8,0,"release")
        i+=1
    return 1


@c_abi_export("pcc_exp2_leaf_refill")
def pcc_exp2_leaf_refill(s) -> i64:
    n: i64=load_i64(s,PAGE_COUNT)
    i: i64=load_i64(s,NEXT_REFILL)
    meta=load_ptr(s,META)
    while i<n:
        if atomic_load_i64(meta,i*META_BYTES,"acquire")==EMPTY:
            # Every logical allocation is initially zero; py_str_new(NULL,n)
            # relies on calloc semantics. Clear a recycled page only on refill.
            memset(ptr_add(load_ptr(s,BASE),i*PAGE_BYTES),0,PAGE_BYTES)
            atomic_store_i64(meta,i*META_BYTES+8,0,"release")
            atomic_store_i64(meta,i*META_BYTES,YOUNG,"release")
            store_i64(s,ACTIVE,i)
            store_i64(s,NEXT_REFILL,i+1)
            return i
        i+=1
    store_i64(s,ACTIVE,-1)
    store_i64(s,NEXT_REFILL,n)
    return -1


@c_abi_export("pcc_exp2_leaf_take")
def pcc_exp2_leaf_take(s,size:i64):
    # Backward-compatible private raw/closed-string fixture only.
    return pcc_exp2_leaf_take_kind(s,size,abi_constant("object.type.str"))


@c_abi_export("pcc_exp2_leaf_take_kind")
def pcc_exp2_leaf_take_kind(s,size:i64,tag:i64):
    if ptr_is_null(s):
        return null()
    stride:i64=load_i64(s,STRIDE)
    if size<1 or size>stride:
        return null()
    if tag==abi_constant("object.type.float"):
        if size!=24 or stride!=32:
            return null()
    elif tag==abi_constant("object.type.str"):
        if stride<64:
            return null()
    else:
        return null()
    page:i64=load_i64(s,ACTIVE)
    meta=load_ptr(s,META)
    if (page < 0
            or atomic_load_i64(meta, page * META_BYTES, "acquire") != YOUNG
            or atomic_load_i64(meta, page * META_BYTES + 8, "acquire") >= PAGE_BYTES):
        page=pcc_exp2_leaf_refill(s)
        if page<0:
            return null()
    used:i64=atomic_load_i64(meta,page*META_BYTES+8,"acquire")
    cell=ptr_add(load_ptr(s,BASE),page*PAGE_BYTES+used)
    # No external exact positive may precede completion of object header.
    store_i64(cell,0,1)
    store_i32(cell,8,tag)
    store_i32(cell,12,0)
    # A second carrier could expose an object on this active page. A racing
    # write that loses here never publishes its cell. The preceding check stops
    # the next allocation even if exposure raced the final store in this one.
    if atomic_load_i64(meta, page * META_BYTES, "acquire") != YOUNG:
        return null()
    atomic_store_i64(meta, page * META_BYTES + 8, used + stride, "release")
    atomic_store_i64(s, ALLOCATIONS, atomic_load_i64(s, ALLOCATIONS, "relaxed") + 1, "relaxed")
    atomic_store_i64(s, REQUESTED_BYTES, atomic_load_i64(s, REQUESTED_BYTES, "relaxed") + size, "relaxed")
    return cell


@c_abi_export("_pcc_exp2_internal_leaf_page_live")
def _leaf_page_live(state: i64) -> i64:
    return 1 if YOUNG <= state <= EXPOSED else 0


@c_abi_export("pcc_exp2_leaf_find")
def pcc_exp2_leaf_find(s,word,exact:i64) -> i64:
    if ptr_is_null(word):
        return -1
    # Value-only classification; interior conservative words retain only inside
    # the published bump range, not a slab's unused tail or metadata.
    offset:i64=ptr_diff(word,load_ptr(s,BASE))
    if offset<0 or offset>=load_i64(s,PAGE_COUNT)*PAGE_BYTES:
        return -1
    if exact!=0 and (offset&(load_i64(s,STRIDE)-1))!=0:
        return -1
    i:i64=offset//PAGE_BYTES
    meta=load_ptr(s,META)
    if _leaf_page_live(atomic_load_i64(meta, i * META_BYTES, "acquire")) == 0:
        return -1
    if (offset&(PAGE_BYTES-1))>=atomic_load_i64(meta,i*META_BYTES+8,"acquire"):
        return -1
    return i


@c_abi_export("pcc_exp2_leaf_is_start")
def pcc_exp2_leaf_is_start(s,word) -> i64:
    if ptr_is_null(s):
        return 0
    if pcc_exp2_leaf_find(s,word,1)>=0:
        return 1
    return 0


@c_abi_export("pcc_exp2_leaf_remember")
def pcc_exp2_leaf_remember(s,stable_slot)->i64:
    if ptr_is_null(s):
        return 0
    if ptr_is_null(stable_slot):
        store_i64(s,UNSAFE_CUT,1)
        return 0
    addr:i64=ptr_to_int(stable_slot)
    n:i64=load_i64(s,REM_COUNT)
    rem=load_ptr(s,REMEMBERED)
    i:i64=0
    while i<n:
        if load_i64(rem,8*i)==addr:
            return 1
        i+=1
    if n>=load_i64(s,REM_CAP):
        store_i64(s,UNSAFE_CUT,1)
        return 0
    store_ptr(rem,8*n,stable_slot)
    store_i64(s,REM_COUNT,n+1)
    return 1


@c_abi_export("pcc_exp2_leaf_mark_word")
def pcc_exp2_leaf_mark_word(s,word)->None:
    i:i64=pcc_exp2_leaf_find(s,word,0)
    if i<0:
        return
    meta=load_ptr(s,META)
    atomic_cas_i64(meta, i * META_BYTES, YOUNG, SEEN_YOUNG, "acq_rel", "acquire")


@c_abi_export("pcc_exp2_leaf_minor")
def pcc_exp2_leaf_minor(s,roots,n:i64)->i64:
    if ptr_is_null(s) or n<0 or n>16777216 or (n!=0 and ptr_is_null(roots)):
        return -1
    if load_i64(s,UNSAFE_CUT)!=0:
        return -1
    i:i64=0
    while i<n:
        pcc_exp2_leaf_mark_word(s,load_ptr(roots,i*8))
        i+=1
    rem=load_ptr(s,REMEMBERED)
    m:i64=load_i64(s,REM_COUNT)
    i=0
    while i<m:
        slot=load_ptr(rem,i*8)
        pcc_exp2_leaf_mark_word(s,load_ptr(slot,0))
        i+=1
    meta=load_ptr(s,META)
    count:i64=load_i64(s,PAGE_COUNT)
    freed:i64=0
    promoted:i64=0
    i=0
    while i<count:
        offset: i64 = i * META_BYTES
        state: i64 = atomic_load_i64(meta, offset, "acquire")
        if state == YOUNG:
            state = atomic_cas_i64(meta, offset, YOUNG, RECYCLING, "acq_rel", "acquire")
            if state == YOUNG:
                # Publication preceded the clear; a racing exposure cannot
                # mistake a recycling page for a valid, retained one.
                atomic_store_i64(meta, offset + 8, 0, "release")
                atomic_store_i64(meta, offset, EMPTY, "release")
                freed += 1
        if state == SEEN_YOUNG or state == EXPOSED:
            atomic_store_i64(meta, offset, OLD, "release")
            promoted += 1
            occupied: i64 = atomic_load_i64(meta, offset + 8, "acquire") // load_i64(s, STRIDE)
            store_i64(s, PROMOTED_OCCUPIED_CELLS, load_i64(s, PROMOTED_OCCUPIED_CELLS) + occupied)
        i+=1
    store_i64(s,MINORS,load_i64(s,MINORS)+1)
    store_i64(s,SLOTS_VISITED,load_i64(s,SLOTS_VISITED)+n+m)
    store_i64(s,PAGES_RECYCLED,load_i64(s,PAGES_RECYCLED)+freed)
    store_i64(s,PAGES_PROMOTED,load_i64(s,PAGES_PROMOTED)+promoted)
    store_i64(s,ACTIVE,-1)
    store_i64(s,NEXT_REFILL,0)
    # Retained pages have promoted; fresh novel old->young stores must re-dirty.
    store_i64(s,REM_COUNT,0)
    return freed

# Activation is explicitly restricted to private synchronous no-escape test
# domains with stable roots, not enabled for arbitrary application code.
define_thread_local_ptr_null("pcc_exp2_leaf_bound_state")
define_global_i64("pcc_exp2_leaf_carrier_domain", 0)
# The fixture directory and the contiguous process directory must not coexist.
define_global_i64("pcc_exp2_domain_install_kind", 0)
pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)


@c_abi_export("pcc_exp2_leaf_bind")
def pcc_exp2_leaf_bind(s)->i64:
    if (ptr_is_null(s) or pcc_gc_backend()!=3 or
            not ptr_is_null(global_load_ptr("pcc_exp2_leaf_bound_state"))):
        return 0
    # TLS alone cannot prevent two carriers binding the same mutable cursor.
    if atomic_cas_i64(s,OWNER,0,1,"acq_rel","acquire")!=0:
        return 0
    global_store_ptr("pcc_exp2_leaf_bound_state",s)
    return 1


@c_abi_export("pcc_exp2_leaf_unbind")
def pcc_exp2_leaf_unbind(s)->i64:
    if (ptr_is_null(s) or ptr_to_int(s)!=ptr_to_int(global_load_ptr("pcc_exp2_leaf_bound_state"))):
        return 0
    global_store_ptr("pcc_exp2_leaf_bound_state",null())
    atomic_store_i64(s,OWNER,0,"release")
    return 1


@c_abi_export("pcc_exp2_leaf_try")
def pcc_exp2_leaf_try(n:i64):
    return pcc_exp2_leaf_try_kind(n,abi_constant("object.type.str"))


@c_abi_export("pcc_exp2_leaf_try_kind")
def pcc_exp2_leaf_try_kind(n:i64,tag:i64):
    s=global_load_ptr("pcc_exp2_leaf_bound_state")
    if ptr_is_null(s):
        return null()
    return pcc_exp2_leaf_take_kind(s,n,tag)


@c_abi_export("pcc_exp2_leaf_bound_start")
def pcc_exp2_leaf_bound_start(word)->i64:
    s=global_load_ptr("pcc_exp2_leaf_bound_state")
    if not ptr_is_null(s) and pcc_exp2_leaf_is_start(s,word)!=0:
        return 1
    published= int_to_ptr(atomic_load_i64(global_addr("pcc_exp2_leaf_carrier_domain"),0,"acquire"))
    if ptr_is_null(published):
        return 0
    n:i64=load_i64(published,0)
    i:i64=0
    while i<n:
        if pcc_exp2_leaf_is_start(load_ptr(published,8+i*8),word)!=0:
            return 1
        i+=1
    return 0

@c_abi_export("pcc_exp2_leaf_install")
def pcc_exp2_leaf_install(directory) -> i64:
    """Publish fully initialized disjoint arena descriptions for process lifetime.
    Each descriptor and the directory must outlive every carrier. This small
    fixed domain is intentionally single-install; there is no concurrent removal.
    Only its original mutator writes a bump cursor. All collectors must park all
    mutators before recycling pages. No cell is registered or individually pinned.
    """
    if ptr_is_null(directory) or pcc_gc_backend()!=3:
        return 0
    n:i64=load_i64(directory,0)
    if n<1 or n>128:
        return 0
    i:i64=0
    while i<n:
        cur=load_ptr(directory,8+i*8)
        if ptr_is_null(cur):
            return 0
        base:i64=ptr_to_int(load_ptr(cur,BASE))
        pages:i64=load_i64(cur,PAGE_COUNT)
        if base<=0 or (base&7)!=0 or pages<=0 or pages>1048576 or base>9223372036854775807-pages*PAGE_BYTES:
            return 0
        j:i64=0
        while j<i:
            prior=load_ptr(directory,8+j*8)
            other:i64=ptr_to_int(load_ptr(prior,BASE))
            if base<other+load_i64(prior,PAGE_COUNT)*PAGE_BYTES and other<base+pages*PAGE_BYTES:
                return 0
            j+=1
        i+=1
    if atomic_cas_i64(global_addr("pcc_exp2_domain_install_kind"), 0, 0, 1, "acq_rel", "acquire") != 0:
        return 0
    atomic_store_i64(global_addr("pcc_exp2_leaf_carrier_domain"), 0, ptr_to_int(directory), "release")
    return 1


# The bounded process opt-in allocates a single zeroed raw block. The 2 lanes
# per carrier (exact float32 and str128), their descriptors, page metadata and
# mark scratch have process lifetime. A user of *this private ABI* must only
# admit registered mutators and supply full heap/foreign edge enforcement.
_PROCESS_MAX_CARRIERS = 64
_PROCESS_MAX_PAGES = 256  # per size class per carrier
_PROCESS_REM_SLOTS = 1  # shape only: external gate owns the actual old edges
_PROCESS_BASE = 0
_PROCESS_STATES = 8
_PROCESS_MARKS = 16
_PROCESS_CLASS_BYTES = 24
_PROCESS_ARENA_BYTES = 32
_PROCESS_RESERVE_BYTES = 40
_PROCESS_CARRIERS = 48
_PROCESS_NEXT_LANE = 56
_PROCESS_META = 64
_PROCESS_TAINTED = 72
_PROCESS_MINORS = 80
_PROCESS_SUM_NS = 88
_PROCESS_MAX_US = 96
_PROCESS_RECYCLED = 104
_PROCESS_PROMOTED = 112
_PROCESS_OCCUPIED = 120
_PROCESS_REFUSALS = 128
_PROCESS_EXPOSURES = 136
_PROCESS_LAST_EPOCH = 144
_PROCESS_STAMP = 152
_PROCESS_HEADER_BYTES = 160
_PROCESS_CUT_DOMAIN = 0
_PROCESS_CUT_STAMP = 8
_PROCESS_CUT_BYTES = 16

define_global_i64("pcc_exp2_process_directory", 0)
define_global_i64("pcc_exp2_process_gate_fn", 0)
define_global_ptr_null("pcc_exp2_process_gate_user")
define_thread_local_ptr_null("pcc_exp2_process_lane")
define_thread_local_ptr_null("pcc_exp2_process_cut")

pcc_exp2_exposure_is_revoked = extern("pcc_exp2_exposure_is_revoked",(),c_int64)
pcc_thread_is_running = extern("pcc_thread_is_running", (), c_int64)
pcc_gc_visit_stopped_root_cut = extern("pcc_gc_visit_stopped_root_cut",(c_int64,c_ptr,c_ptr,c_ptr),c_int64)
pcc_platform_monotonic_ns = extern("pcc_platform_monotonic_ns",(),c_int64)
pcc_thread_world_stop_epoch_held = extern("pcc_thread_world_stop_epoch_held", (), c_int64)


@c_abi_export("_pcc_exp2_internal_process_directory")
def _process_directory():
    return int_to_ptr(atomic_load_i64(global_addr("pcc_exp2_process_directory"), 0, "acquire"))


@c_abi_export("pcc_exp2_process_enable")
def pcc_exp2_process_enable(carriers: i64, pages_per_class: i64) -> i64:
    """Return 1 only for one successful GC3 installation. Never free it."""
    if (carriers < 1 or carriers > _PROCESS_MAX_CARRIERS or pages_per_class < 1
            or pages_per_class > _PROCESS_MAX_PAGES
            or atomic_load_i32(global_addr("pcc_gc_backend_frozen"), 0, "acquire") == 0
            or pcc_gc_backend() != 3):
        return 0
    owner = global_addr("pcc_exp2_domain_install_kind")
    if atomic_cas_i64(owner, 0, 0, 2, "acq_rel", "acquire") != 0:
        return 0
    classes: i64 = carriers * 2
    pages: i64 = classes * pages_per_class
    class_bytes: i64 = pages_per_class * PAGE_BYTES
    arena_bytes: i64 = pages * PAGE_BYTES
    prefix: i64 = (_PROCESS_HEADER_BYTES + classes * (STATE_BYTES + _PROCESS_REM_SLOTS * 8)
                   + pages * (META_BYTES + 8))
    reserve_bytes: i64 = prefix + PAGE_BYTES - 1 + arena_bytes
    d = calloc(1, reserve_bytes)
    if ptr_is_null(d):
        atomic_store_i64(owner, 0, 0, "release")
        return 0
    states = ptr_add(d, _PROCESS_HEADER_BYTES)
    meta = ptr_add(states, classes * STATE_BYTES)
    marks = ptr_add(meta, pages * META_BYTES)
    remembered = ptr_add(marks, pages * 8)
    arena = int_to_ptr((ptr_to_int(d) + prefix + PAGE_BYTES - 1) & -PAGE_BYTES)
    store_ptr(d, _PROCESS_BASE, arena)
    store_ptr(d, _PROCESS_STATES, states)
    store_ptr(d, _PROCESS_META, meta)
    store_ptr(d, _PROCESS_MARKS, marks)
    store_i64(d, _PROCESS_CARRIERS, carriers)
    store_i64(d, _PROCESS_CLASS_BYTES, class_bytes)
    store_i64(d, _PROCESS_ARENA_BYTES, arena_bytes)
    store_i64(d, _PROCESS_RESERVE_BYTES, reserve_bytes)
    i: i64 = 0
    while i < classes:
        stride: i64 = 32 if (i & 1) == 0 else 128
        if pcc_exp2_leaf_init_sized(
            ptr_add(states, i * STATE_BYTES), ptr_add(arena, i * class_bytes),
            ptr_add(meta, i * pages_per_class * META_BYTES), pages_per_class,
            ptr_add(remembered, i * _PROCESS_REM_SLOTS * 8), _PROCESS_REM_SLOTS, stride,
        ) == 0:
            free(d)
            atomic_store_i64(owner, 0, 0, "release")
            return 0
        i += 1
    atomic_store_i64(global_addr("pcc_exp2_process_directory"), 0, ptr_to_int(d), "release")
    return 1


@c_abi_export("pcc_exp2_process_register_gate")
def pcc_exp2_process_register_gate(fn, userdata) -> i64:
    """Write-once registration. The C callback and userdata outlive the pool.

    Callback ABI: (int64_t stopped_epoch, void *userdata) -> int64_t.
    Exact 1 means it has covered all otherwise-hidden edges and marked any
    positives for this call. No allocation, managed callbacks, root mutation,
    world resume or freeing is allowed. 0 or negative fails closed.
    """
    bits: i64 = ptr_to_int(fn)
    if bits == 0 or bits == 1:
        return 0
    slot = global_addr("pcc_exp2_process_gate_fn")
    if atomic_cas_i64(slot, 0, 0, 1, "acq_rel", "acquire") != 0:
        return 0
    global_store_ptr("pcc_exp2_process_gate_user", userdata)
    atomic_store_i64(slot, 0, bits, "release")
    return 1


@c_abi_export("_pcc_exp2_internal_process_claim_lane")
def _process_claim_lane(d):
    next_lane: i64 = atomic_load_i64(d, _PROCESS_NEXT_LANE, "acquire")
    lanes: i64 = load_i64(d, _PROCESS_CARRIERS)
    while next_lane < lanes:
        observed: i64 = atomic_cas_i64(d, _PROCESS_NEXT_LANE, next_lane, next_lane + 1, "acq_rel", "acquire")
        if observed == next_lane:
            lane = ptr_add(load_ptr(d, _PROCESS_STATES), next_lane * 2 * STATE_BYTES)
            global_store_ptr("pcc_exp2_process_lane", lane)
            return lane
        next_lane = observed
    return null()


@c_abi_export("pcc_exp2_process_take")
def pcc_exp2_process_take(size: i64, tag: i64):
    """Try a cell on the calling registered carrier; NULL means stock fallback."""
    d = _process_directory()
    if (ptr_is_null(d) or pcc_thread_is_running()==0 or pcc_exp2_exposure_is_revoked()!=0 or not ptr_is_null(global_load_ptr("pcc_exp2_leaf_bound_state"))
            or not ptr_is_null(global_load_ptr("pcc_exp2_process_cut"))
            or atomic_load_i64(d, _PROCESS_TAINTED, "acquire") != 0):
        return null()
    kind: i64 = 0
    if tag == abi_constant("object.type.float"):
        if size != 24:
            return null()
    elif tag == abi_constant("object.type.str"):
        if size < 41 or size > 128:
            return null()
        kind = 1
    else:
        return null()
    lane = global_load_ptr("pcc_exp2_process_lane")
    if ptr_is_null(lane):
        lane = _process_claim_lane(d)
        if ptr_is_null(lane):
            return null()
    return pcc_exp2_leaf_take_kind(ptr_add(lane, kind * STATE_BYTES), size, tag)


@c_abi_export("_pcc_exp2_internal_process_offset")
def _process_offset(d, word) -> i64:
    if ptr_is_null(d) or ptr_is_null(word):
        return -1
    offset: i64 = ptr_diff(word, load_ptr(d, _PROCESS_BASE))
    if offset < 0 or offset >= load_i64(d, _PROCESS_ARENA_BYTES):
        return -1
    return offset


@c_abi_export("_pcc_exp2_internal_process_page")
def _process_page(d, offset: i64):
    return ptr_add(load_ptr(d, _PROCESS_META), (offset // PAGE_BYTES) * META_BYTES)


@c_abi_export("_pcc_exp2_internal_process_occupied")
def _process_occupied(page, offset: i64) -> i64:
    if _leaf_page_live(atomic_load_i64(page, 0, "acquire")) == 0:
        return 0
    return 1 if (offset & (PAGE_BYTES - 1)) < atomic_load_i64(page, 8, "acquire") else 0


@c_abi_export("pcc_exp2_process_is_start")
def pcc_exp2_process_is_start(word) -> i64:
    d = _process_directory()
    offset: i64 = _process_offset(d, word)
    if offset < 0:
        return 0
    stride: i64 = 32 if ((offset // load_i64(d, _PROCESS_CLASS_BYTES)) & 1) == 0 else 128
    if (offset & (stride - 1)) != 0:
        return 0
    return _process_occupied(_process_page(d, offset), offset)


@c_abi_export("pcc_exp2_process_contains")
def pcc_exp2_process_contains(word) -> i64:
    d = _process_directory()
    offset: i64 = _process_offset(d, word)
    if offset < 0:
        return 0
    return _process_occupied(_process_page(d, offset), offset)


@c_abi_export("pcc_exp2_process_expose")
def pcc_exp2_process_expose(word, reason: i64) -> i64:
    """Retain the touched page irrevocably, or fail closed and taint the pool.

    Outside the reserved arena, 0. For a present cell (including interior),
    1 means this page will never be recycled. A conflicting/missing in-arena
    extent or explicit reason >= 128 is a permanent denial (-1), not a root.
    Caller handles its ABI boundary if refusal is not safe to ignore.
    """
    d = _process_directory()
    if ptr_is_null(d):
        return 0
    if reason >= 128:
        # Opaque foreign execution can hide an unrelated nursery address.
        atomic_store_i64(d, _PROCESS_TAINTED, 1, "release")
        return -1
    offset: i64 = _process_offset(d, word)
    if offset < 0:
        return 0
    if atomic_load_i64(d, _PROCESS_TAINTED, "acquire") != 0:
        return -1
    page = _process_page(d, offset)
    state: i64 = atomic_load_i64(page, 0, "acquire")
    within: i64 = offset & (PAGE_BYTES - 1)
    while _leaf_page_live(state) != 0 and within < atomic_load_i64(page, 8, "acquire"):
        if state == OLD or state == EXPOSED:
            return 1
        changed: i64 = atomic_cas_i64(page, 0, state, EXPOSED, "acq_rel", "acquire")
        if changed == state:
            atomic_rmw_i64("add", d, _PROCESS_EXPOSURES, 1, "relaxed")
            return 1
        state = changed
    atomic_store_i64(d, _PROCESS_TAINTED, 1, "release")
    return -1


@c_abi_export("pcc_exp2_process_mark")
def pcc_exp2_process_mark(word, context) -> None:
    """Record a positive only on the stop owner, in its private pending cut.

    `context` intentionally ignored so the safety-gate callback can use its
    own context. A mark is serial, O(1), requires no object/header read, and
    cannot confer any survival age before both root-cut gates succeed.
    """
    cut = global_load_ptr("pcc_exp2_process_cut")
    if ptr_is_null(cut):
        return
    d = load_ptr(cut, _PROCESS_CUT_DOMAIN)
    offset: i64 = _process_offset(d, word)
    if offset < 0:
        return
    page = _process_page(d, offset)
    if (atomic_load_i64(page, 0, "acquire") == YOUNG
            and (offset & (PAGE_BYTES - 1)) < atomic_load_i64(page, 8, "acquire")):
        store_i64(load_ptr(d, _PROCESS_MARKS), (offset // PAGE_BYTES) * 8,
                  load_i64(cut, _PROCESS_CUT_STAMP))


@c_abi_export("_pcc_exp2_internal_process_refuse")
def _process_refuse(d) -> i64:
    if not ptr_is_null(d):
        atomic_rmw_i64("add", d, _PROCESS_REFUSALS, 1, "relaxed")
    return -1


@c_abi_export("_pcc_exp2_internal_process_monotonic_ns")
def _process_monotonic_ns() -> i64:
    return pcc_platform_monotonic_ns()


@c_abi_export("pcc_exp2_process_minor_held")
def pcc_exp2_process_minor_held(epoch: i64) -> i64:
    """Return recycled pages only after both cuts in the same owned stop.

    Call before resume and without holding any unrelated mutator lock. One
    successful batch is allowed in an epoch; rejected partial marks are not
    transferred. The process API does not initiate a stop or trigger collection.
    """
    d = _process_directory()
    if (ptr_is_null(d) or epoch <= 0 or pcc_exp2_exposure_is_revoked()!=0 or not ptr_is_null(global_load_ptr("pcc_exp2_process_cut"))
            or pcc_thread_world_stop_epoch_held() != epoch
            or atomic_load_i64(d, _PROCESS_TAINTED, "acquire") != 0
            or atomic_load_i64(d, _PROCESS_LAST_EPOCH, "acquire") == epoch):
        return _process_refuse(d)
    gate: i64 = atomic_load_i64(global_addr("pcc_exp2_process_gate_fn"), 0, "acquire")
    if gate == 0 or gate == 1:
        return _process_refuse(d)
    start_ns: i64 = _process_monotonic_ns()
    # All temporary roots are written only into per-page raw scratch, stamped
    # by a fresh attempt even when the next cut is in the same still-held epoch.
    stamp: i64 = load_i64(d, _PROCESS_STAMP)
    if stamp == 9223372036854775807:
        return _process_refuse(d)
    stamp += 1
    store_i64(d, _PROCESS_STAMP, stamp)
    cut = stack_alloc(_PROCESS_CUT_BYTES)
    store_ptr(cut, _PROCESS_CUT_DOMAIN, d)
    store_i64(cut, _PROCESS_CUT_STAMP, stamp)
    visitor = function_addr("pcc_exp2_process_mark")
    global_store_ptr("pcc_exp2_process_cut", cut)
    seen: i64 = pcc_gc_visit_stopped_root_cut(epoch, visitor, visitor, null())
    safe: i64 = 0
    if seen == epoch and pcc_thread_world_stop_epoch_held() == epoch:
        safe = call_i64_i64_ptr(int_to_ptr(gate), epoch, global_load_ptr("pcc_exp2_process_gate_user"))
    global_store_ptr("pcc_exp2_process_cut", null())
    if (safe != 1 or pcc_exp2_exposure_is_revoked()!=0 or pcc_thread_world_stop_epoch_held() != epoch
            or atomic_load_i64(d, _PROCESS_TAINTED, "acquire") != 0):
        return _process_refuse(d)
    # Validate every state before the first retired page. The internal fixture
    # remembered-slot protocol is never an authorization for process edges.
    n: i64 = load_i64(d, _PROCESS_CARRIERS) * 2
    states = load_ptr(d, _PROCESS_STATES)
    i: i64 = 0
    while i < n:
        s = ptr_add(states, i * STATE_BYTES)
        if load_i64(s, UNSAFE_CUT) != 0 or load_i64(s, REM_COUNT) != 0:
            return _process_refuse(d)
        i += 1
    marks = load_ptr(d, _PROCESS_MARKS)
    meta = load_ptr(d, _PROCESS_META)
    count: i64 = load_i64(d, _PROCESS_ARENA_BYTES) // PAGE_BYTES
    i = 0
    while i < count:
        if load_i64(marks, i * 8) == stamp:
            atomic_cas_i64(meta, i * META_BYTES, YOUNG, SEEN_YOUNG, "acq_rel", "acquire")
        i += 1
    recycled: i64 = 0
    promoted: i64 = 0
    occupied: i64 = 0
    i = 0
    while i < n:
        s = ptr_add(states, i * STATE_BYTES)
        old_pages: i64 = load_i64(s, PAGES_PROMOTED)
        old_cells: i64 = load_i64(s, PROMOTED_OCCUPIED_CELLS)
        recycled += pcc_exp2_leaf_minor(s, null(), 0)
        promoted += load_i64(s, PAGES_PROMOTED) - old_pages
        occupied += load_i64(s, PROMOTED_OCCUPIED_CELLS) - old_cells
        i += 1
    atomic_store_i64(d, _PROCESS_LAST_EPOCH, epoch, "release")
    atomic_rmw_i64("add", d, _PROCESS_RECYCLED, recycled, "relaxed")
    atomic_rmw_i64("add", d, _PROCESS_PROMOTED, promoted, "relaxed")
    atomic_rmw_i64("add", d, _PROCESS_OCCUPIED, occupied, "relaxed")
    elapsed_ns: i64 = _process_monotonic_ns() - start_ns
    if start_ns > 0 and elapsed_ns > 0:
        us: i64 = elapsed_ns // 1000 + (1 if elapsed_ns % 1000 != 0 else 0)
        atomic_rmw_i64("add", d, _PROCESS_SUM_NS, elapsed_ns, "relaxed")
        if us > atomic_load_i64(d, _PROCESS_MAX_US, "relaxed"):
            atomic_store_i64(d, _PROCESS_MAX_US, us, "relaxed")
    atomic_rmw_i64("add", d, _PROCESS_MINORS, 1, "release")
    return recycled


@c_abi_export("pcc_exp2_process_metric")
def pcc_exp2_process_metric(index: i64) -> i64:
    """Diagnostic one-counter snapshot (read after workers stop for sums)."""
    d = _process_directory()
    if ptr_is_null(d):
        return 0
    if index == 2:
        return atomic_load_i64(d, _PROCESS_MINORS, "acquire")
    if index == 3:
        return atomic_load_i64(d, _PROCESS_SUM_NS, "relaxed")
    if index == 4:
        return atomic_load_i64(d, _PROCESS_MAX_US, "relaxed")
    if index == 5:
        return atomic_load_i64(d, _PROCESS_RECYCLED, "relaxed")
    if index == 6:
        return atomic_load_i64(d, _PROCESS_PROMOTED, "relaxed")
    if index == 7:
        return atomic_load_i64(d, _PROCESS_OCCUPIED, "relaxed")
    if index == 8:
        return atomic_load_i64(d, _PROCESS_NEXT_LANE, "acquire")
    if index == 9:
        return load_i64(d, _PROCESS_ARENA_BYTES)
    if index == 10:
        return load_i64(d, _PROCESS_RESERVE_BYTES)
    if index == 11:
        return atomic_load_i64(d, _PROCESS_REFUSALS, "relaxed")
    if index == 12:
        return atomic_load_i64(d, _PROCESS_EXPOSURES, "relaxed")
    if index == 13:
        return atomic_load_i64(d, _PROCESS_TAINTED, "acquire")
    if index == 18:
        return atomic_load_i64(d, _PROCESS_LAST_EPOCH, "acquire")
    field: i64 = ALLOCATIONS
    step: i64 = 1
    i: i64 = 0
    if index == 1 or index == 15 or index == 17:
        field = REQUESTED_BYTES
    if index >= 14 and index <= 17:
        step = 2
        if index >= 16:
            i = 1
    elif index != 0 and index != 1:
        return -1
    count: i64 = load_i64(d, _PROCESS_CARRIERS) * 2
    states = load_ptr(d, _PROCESS_STATES)
    total: i64 = 0
    while i < count:
        total += atomic_load_i64(states, i * STATE_BYTES + field, "relaxed")
        i += step
    return total
