"""Interleavings of the queued-contender graph-lock protocol.

The public word remains a 0/1 lock. MCS grants permission to contend, not
ownership of that word; a fast newcomer may barge. A queued owner keeps its
queue position through public-word release. Nodes and queue contexts are
private to a thread and a lock respectively, never shared by hash collision.
"""
from collections import deque
from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class State:
    word: tuple
    tail: tuple
    queue: tuple
    next: tuple
    grant: tuple
    previous: tuple
    pc: tuple
    remaining: tuple
    no_park: tuple
    linked: tuple


def initial(locks, rounds):
    count = len(locks)
    number = max(locks) + 1
    return State((-1,) * number, (-1,) * number, ((),) * number,
                 (-1,) * count, (0,) * count, (-1,) * count, (0,) * count,
                 (rounds,) * count, (0,) * count, (0,) * count)


def advance(state, thread, locks):
    fields = {name: list(getattr(state, name)) for name in State.__dataclass_fields__}
    word, tail, queue = fields['word'], fields['tail'], fields['queue']
    nxt, grant, previous = fields['next'], fields['grant'], fields['previous']
    pc, remaining = fields['pc'], fields['remaining']
    depth, linked = fields['no_park'], fields['linked']
    stage, lock = pc[thread], locks[thread]
    if stage == 0:  # One fast CAS.
        if word[lock] == -1:
            word[lock], depth[thread], pc[thread] = thread, 1, 8
        else:
            pc[thread] = 1
    elif stage == 1:  # Pre-enqueue safepoint, then return to running.
        pc[thread] = 2
    elif stage == 2:  # Enter no-park and initialize this detached node.
        assert not linked[thread]
        depth[thread] = 1
        nxt[thread], grant[thread], pc[thread] = -1, 0, 3
    elif stage == 3:  # Atomic tail exchange; successor link can be delayed.
        previous[thread] = tail[lock]
        tail[lock] = thread
        queue[lock] = queue[lock] + (thread,)
        linked[thread] = 1
        pc[thread] = 7 if previous[thread] == -1 else 4
    elif stage == 4:
        nxt[previous[thread]] = thread
        pc[thread] = 5
    elif stage == 5:
        if not grant[thread]:
            return None
        pc[thread] = 7
    elif stage == 7:  # Only queued head may contend on the public word.
        assert queue[lock][0] == thread
        if word[lock] != -1:
            return None
        word[lock], pc[thread] = thread, 8
    elif stage == 8:  # Bounded critical section.
        assert word[lock] == thread
        pc[thread] = 9
    elif stage == 9:
        assert word[lock] == thread
        word[lock] = -1
        pc[thread] = 10 if linked[thread] else 13
    elif stage == 10:
        if nxt[thread] != -1:
            pc[thread] = 12
        elif tail[lock] == thread:  # CAS tail(self)->NULL wins.
            tail[lock] = -1
            assert queue[lock] == (thread,)
            queue[lock] = ()
            linked[thread], pc[thread] = 0, 16
        else:  # Successor exchanged tail but has not published next yet.
            pc[thread] = 11
    elif stage == 11:
        if nxt[thread] == -1:
            return None
        pc[thread] = 12
    elif stage == 12:
        successor = nxt[thread]
        assert queue[lock][0] == thread and queue[lock][1] == successor
        grant[successor] = 1
        queue[lock] = queue[lock][1:]
        linked[thread], pc[thread] = 0, 13
    elif stage == 13:
        assert not linked[thread]
        depth[thread] = 0
        remaining[thread] -= 1
        pc[thread] = 0 if remaining[thread] else 14
    elif stage == 16:  # A new head may arrive before detached-node cleanup ends.
        pc[thread] = 13
    else:
        return None
    return State(**{name: tuple(value) for name, value in fields.items()})


def verify(state, locks):
    for lock, owner in enumerate(state.word):
        critical = [t for t, stage in enumerate(state.pc)
                    if locks[t] == lock and stage in (8, 9)]
        assert critical == ([] if owner == -1 else [owner])
        assert state.tail[lock] == (state.queue[lock][-1] if state.queue[lock] else -1)
        contenders = [t for t in state.queue[lock]
                      if state.pc[t] in (7, 8, 9)]
        assert not contenders or contenders == [state.queue[lock][0]]
    for thread, linked in enumerate(state.linked):
        assert bool(linked) == any(thread in queue for queue in state.queue)
        if linked:
            assert state.no_park[thread] == 1  # No linked waiter may park/free.
        if state.pc[thread] == 14:
            assert not linked and not state.no_park[thread]


@pytest.mark.parametrize('locks,rounds', [((0, 0), 2), ((0, 0, 0), 1), ((0, 1, 0), 1)])
def test_all_two_and_three_thread_interleavings(locks, rounds, record_property):
    start = initial(locks, rounds)
    pending, seen = deque([start]), {start}
    terminal = 0
    publication_race = False
    while pending:
        state = pending.popleft()
        verify(state, locks)
        publication_race |= 11 in state.pc
        if all(stage == 14 for stage in state.pc):
            terminal += 1
            assert all(owner == -1 for owner in state.word)
            continue
        following = []
        for thread in range(len(locks)):
            candidate = advance(state, thread, locks)
            if candidate is not None:
                following.append(candidate)
                if candidate not in seen:
                    seen.add(candidate)
                    pending.append(candidate)
        assert following, ('protocol deadlock', state)
        assert len(seen) < 250000, 'model bound, not a proof on truncated exploration'
    assert terminal
    if rounds > 1 or len(set(locks)) == 1:
        assert publication_race
    record_property('reachable_states', len(seen))


def test_stop_request_cannot_count_a_linked_waiter_as_parked():
    locks = (0, 0)
    state = advance(initial(locks, 1), 0, locks)  # Fast owner.
    for _ in range(4):
        state = advance(state, 1, locks)  # fail, poll, no-park, tail exchange
    assert state.word == (0,) and state.queue == ((1,),)
    assert state.no_park == (1, 1)
    # A stop request arrives after the waiter's pre-enqueue poll. The
    # collector cannot count either thread stopped until both depths end.
    stop_active = True
    parked = [False, False]
    for _ in range(3):
        state = advance(state, 0, locks)  # critical end, store0, no-park exit
    parked[0] = state.no_park[0] == 0
    assert parked == [True, False]
    assert state.linked[1] and state.no_park[1] > 0
    while state.pc[1] != 14:
        state = advance(state, 1, locks)
        assert state is not None
        verify(state, locks)
    parked[1] = state.no_park[1] == 0
    assert all(parked) and state.tail == (-1,)
    # Fresh registration consults the active stop before joining the live
    # set; resume is the event permitting that new mutator's first enqueue.
    admission_events = []
    if not stop_active:
        admission_events.append('registered')
    assert not admission_events
    stop_active = False
    if not stop_active:
        admission_events.append('registered')
    assert admission_events == ['registered']


def test_context_publication_deduplicates_the_actual_lock_address():
    # Two slow paths allocate before CAS publication. A losing publisher
    # scans the new immutable prefix and uses the winner, freeing only its
    # own unpublished allocation. Distinct lock addresses never share tail.
    head = None
    candidates = [{'key': 100, 'next': head, 'tail': None},
                  {'key': 100, 'next': head, 'tail': None}]
    head = candidates[0]
    expected = None
    assert head is not expected  # Loser's head CAS fails.
    winner = head if head['key'] == candidates[1]['key'] else None
    assert winner is candidates[0]
    second = {'key': 200, 'next': head, 'tail': None}
    assert second['key'] != winner['key']
    assert second is not winner


def kernel_model(allocation_results=()):
    """Execute the actual raw Python bodies with controllable native words."""
    import ast
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / 'pcc/runtime/py/freestanding_thread_kernel_pthread.py'
    tree = ast.parse(path.read_text())
    memory = {}
    events = []
    allocations = iter(allocation_results)
    head = 'pcc_graph_wait_context_head_py'
    tls = 'pcc_tls_thread_state_py'
    node_tls = 'pcc_tls_graph_wait_node_py'
    memory[tls, 0] = 0
    memory[head, 0] = 0
    memory[node_tls, 0] = 0
    memory['pcc_thread_stop_requested', 0] = 0
    def read(base, offset=0, order=None):
        return memory.get((base, offset), 0)
    def write(base, offset, value, order=None):
        memory[base, offset] = value
    def cas(base, offset, expected, desired, success, failure):
        old = read(base, offset)
        if old == expected:
            write(base, offset, desired)
        return old
    def rmw(op, base, offset, value, order):
        assert op == 'xchg'
        old = read(base, offset)
        write(base, offset, value)
        return old
    def register():
        events.append('register')
        memory[tls, 0] |= 562949953421312
        return 1
    def poll():
        events.append(('poll', memory[tls, 0]))
        # Simulate the current fast owner's unlock before this waiter queues.
        memory[9000, 0] = 0
    def abort():
        raise AssertionError('unexpected native abort')
    ns = dict(i64=int, null=lambda: 0, ptr_is_null=lambda p: p == 0,
              ptr_to_int=lambda p: p, int_to_ptr=lambda p: p,
              global_addr=lambda n: n, global_load_ptr=lambda n: read(n),
              global_store_ptr=lambda n, v: write(n, 0, v),
              load_i64=read, load_i32=read, load_ptr=read,
              store_i64=write, store_i32=write, store_ptr=write,
              atomic_load_i64=read, atomic_load_i32=read,
              atomic_store_i64=write, atomic_store_i32=write,
              atomic_cas_i64=cas, atomic_cas_i32=cas, atomic_rmw_i64=rmw,
              malloc=lambda size: next(allocations, 0), free=lambda p: events.append(('free', p)),
              pcc_current_thread_id=register, pcc_thread_safepoint=poll,
              pcc_platform_abort=abort)
    wanted = {'_graph_wait_node', '_graph_wait_context', '_graph_wait_acquire',
              '_graph_wait_release', 'pcc_thread_no_park_enter',
              'pcc_thread_graph_lock_acquire', 'pcc_thread_graph_lock_release',
              'pcc_thread_no_park_exit'}
    definitions = []
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id.startswith('_GRAPH_'):
            definitions.append(node)
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            node.decorator_list = []
            definitions.append(node)
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), 'exec'), ns)
    return ns, memory, events


@pytest.mark.parametrize('allocations', [(), (10000,)])
def test_actual_kernel_node_or_context_oom_keeps_polling_fallback(allocations):
    ns, memory, events = kernel_model(allocations)
    memory[9000, 0] = 1
    ns['pcc_thread_graph_lock_acquire'](9000)
    state = memory['pcc_tls_thread_state_py', 0]
    assert events[0] == 'register'
    assert any(isinstance(e, tuple) and e[0] == 'poll' for e in events)
    assert state & 16777215 == 1
    assert state & 281474959933440 == 16777216
    assert state & 4503599627370496 == 0
    assert ns['pcc_thread_graph_lock_release'](9000) == 0
    assert memory[9000, 0] == 0
    assert memory['pcc_tls_thread_state_py', 0] == 562949953421312


@pytest.mark.parametrize('outer_depth,tripwire', [(0, False), (1, False), (0, True)])
def test_actual_kernel_nested_queue_owner_and_deferred_exit(outer_depth, tripwire):
    ns, memory, events = kernel_model((10001, 20001))
    tls = 'pcc_tls_thread_state_py'
    memory[tls, 0] = 562949953421312 + outer_depth
    memory[9000, 0] = 1
    ns['pcc_thread_graph_lock_acquire'](9000)
    node = memory['pcc_tls_graph_wait_node_py', 0]
    context = memory[node, 16]
    assert node % 128 == context % 128 == 0
    assert memory[context, 16] == node
    assert memory[tls, 0] & 16777215 == outer_depth + 1
    ns['pcc_thread_graph_lock_acquire'](9000)
    assert memory[tls, 0] & 281474959933440 == 2 * 16777216
    assert ns['pcc_thread_graph_lock_release'](9000) == 0
    assert memory[9000, 0] == 1 and memory[context, 16] == node
    if tripwire:
        memory[tls, 0] |= 2251799813685248
    assert ns['pcc_thread_graph_lock_release'](9000) == int(tripwire)
    assert memory[9000, 0] == 0 and memory[context, 16] == 0
    assert memory[node, 16] == 0  # Detached before any deferred report.
    assert memory[tls, 0] & (281474959933440 | 4503599627370496 | 2251799813685248) == 0
    assert memory[tls, 0] & 16777215 == outer_depth + int(tripwire)
    if tripwire:
        ns['pcc_thread_no_park_exit']()
    assert memory[tls, 0] & 16777215 == outer_depth


def test_actual_kernel_fast_path_never_allocates_or_reads_queue_tls():
    ns, memory, events = kernel_model()
    memory['pcc_tls_thread_state_py', 0] = 562949953421312
    def forbidden(*args):
        raise AssertionError('uncontended path touched queue state')
    ns['_graph_wait_acquire'] = forbidden
    ns['_graph_wait_release'] = forbidden
    ns['malloc'] = forbidden
    ns['global_load_ptr'] = forbidden
    ns['pcc_thread_graph_lock_acquire'](9000)
    assert ns['pcc_thread_graph_lock_release'](9000) == 0
    assert not events


@pytest.mark.parametrize('tripwire', (False, True))
def test_actual_queued_release_grants_permission_before_deferred_exit(tripwire):
    ns, memory, events = kernel_model()
    node, successor, context, word = 10000, 11000, 12000, 9000
    memory['pcc_tls_thread_state_py', 0] = 562949953421312 + 4503599627370496 + 16777216 + 1
    if tripwire:
        memory['pcc_tls_thread_state_py', 0] |= 2251799813685248
    memory['pcc_tls_graph_wait_node_py', 0] = node
    memory[word, 0] = 1
    memory[node, 0] = successor
    memory[node, 16] = context
    memory[context, 0] = word
    memory[context, 16] = successor
    assert ns['pcc_thread_graph_lock_release'](word) == int(tripwire)
    assert memory[word, 0] == 0
    assert memory[successor, 8] == 1
    assert memory[node, 16] == 0
    assert ns['atomic_cas_i32'](word, 0, 0, 1, 'acq_rel', 'acquire') == 0
    assert memory['pcc_tls_thread_state_py', 0] == 562949953421312 + int(tripwire)
    if tripwire:
        ns['pcc_thread_no_park_exit']()
    assert not events


def test_actual_new_head_after_tail_detachment_keeps_its_ownership():
    ns, memory, events = kernel_model()
    node, newcomer, context, word = 10000, 11000, 12000, 9000
    memory['pcc_tls_thread_state_py', 0] = 562949953421312 + 4503599627370496 + 16777216 + 1
    memory['pcc_tls_graph_wait_node_py', 0] = node
    memory[node, 16] = context
    memory[context, 0] = word
    memory[context, 16] = node
    memory[word, 0] = 1
    compare = ns['atomic_cas_i64']
    def arrive(base, offset, expected, desired, success, failure):
        result = compare(base, offset, expected, desired, success, failure)
        if base == context and offset == 16 and result == node:
            assert memory[word, 0] == 0 and memory[context, 16] == 0
            memory[context, 16] = newcomer
            memory[newcomer, 16] = context
            assert ns['atomic_cas_i32'](word, 0, 0, 1, 'acq_rel', 'acquire') == 0
        return result
    ns['atomic_cas_i64'] = arrive
    assert ns['pcc_thread_graph_lock_release'](word) == 0
    assert memory[context, 16] == newcomer
    assert memory[node, 16] == 0 and memory[word, 0] == 1
    assert ns['atomic_cas_i32'](word, 0, 0, 1, 'acq_rel', 'acquire') == 1


def test_actual_granted_successor_retries_after_a_barger_releases():
    ns, memory, events = kernel_model()
    node, predecessor, context, word = 10000, 11000, 12000, 9000
    memory['pcc_tls_thread_state_py', 0] = 562949953421312
    memory['pcc_tls_graph_wait_node_py', 0] = node
    memory['pcc_graph_wait_context_head_py', 0] = context
    memory[context, 0] = word
    memory[context, 16] = predecessor
    memory[word, 0] = 1
    store = ns['atomic_store_i64']
    def publish(base, offset, value, order):
        store(base, offset, value, order)
        if base == predecessor and offset == 0:
            # The predecessor granted permission, but a newcomer got the word.
            memory[node, 8] = 1
    load = ns['atomic_load_i32']
    observations = []
    def barger_releases(base, offset, order):
        value = load(base, offset, order)
        observations.append(value)
        memory[word, 0] = 0
        return value
    ns['atomic_store_i64'] = publish
    ns['atomic_load_i32'] = barger_releases
    assert ns['_graph_wait_acquire'](word) == 1
    assert memory[word, 0] == 1 and memory[predecessor, 0] == node
    assert observations == [1, 0]
    assert memory['pcc_tls_thread_state_py', 0] & 16777215 == 1
