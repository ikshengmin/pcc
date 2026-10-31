"""Language-neutral instance state/registration, not generic type checking."""
import pytest

from pcc.support.instantiation import InstanceRegistry, InstanceState


def test_request_reuses_first_placeholder_context_and_registration_order():
    registry = InstanceRegistry()
    first, second = object(), object()
    a = registry.request(('decl', ('i32',)), first, 'first location')
    assert registry.request(('decl', ('i32',)), second, 'later location') is a
    assert a.value is first and a.context == 'first location'
    assert a.state is InstanceState.QUEUED
    assert registry.pending == (a,) and len(registry) == 1
    assert registry.get(('missing', ())) is None


def test_same_arguments_do_not_merge_distinct_declarations_or_compilations():
    registry, other = InstanceRegistry(), InstanceRegistry()
    a, b = object(), object()
    x = registry.request((a, ('i64',)), object())
    y = registry.request((b, ('i64',)), object())
    z = other.request((a, ('i64',)), object())
    assert x is not y and x is not z and len(registry) == 2


def test_fifo_drain_discovers_new_work_and_reuses_running_recursion_placeholder():
    registry = InstanceRegistry()
    a = registry.request('a', object())
    b = registry.request('b', object())
    observed = []
    def build(entry):
        assert entry.state is InstanceState.IN_PROGRESS
        observed.append(entry.key)
        if entry is a:
            assert registry.get('a') is a
            assert registry.request('a', object()).value is a.value
            registry.request('c', object())
        return entry.key + '-body'
    assert registry.drain(build) == ['a-body', 'b-body', 'c-body']
    assert observed == ['a', 'b', 'c']
    assert all(e.state is InstanceState.COMPLETE for e in registry.pending)
    assert a.result == 'a-body' and b.result == 'b-body'
    assert registry.request('a', object()) is a
    assert registry.drain(lambda e: pytest.fail('completed instance rebuilt')) == []
    d = registry.request('d', None)
    assert registry.drain(lambda e: None) == [None]
    assert d.state is InstanceState.COMPLETE and d.value is d.result is None


def test_value_view_has_no_second_mutable_cache():
    registry = InstanceRegistry()
    owner, other = object(), object()
    view = registry.value_view(owner)
    assert len(view) == 0 and view.get(('u64',)) is None
    entry = registry.request((owner, ('u64',)), object())
    registry.request((other, ('u64',)), object())
    assert list(view) == [('u64',)] and len(view) == 1
    assert view[('u64',)] is entry.value
    with pytest.raises(TypeError):
        view[('u64',)] = object()
    with pytest.raises(TypeError):
        registry.entries['replacement'] = entry
    assert isinstance(registry.pending, tuple)


@pytest.mark.parametrize('operation', ['get', 'request-same', 'request-new', 'drain', 'view'])
def test_failure_rethrows_original_exception_and_never_drains_tail(operation):
    registry = InstanceRegistry()
    key = ('owner', ('i32',))
    broken = registry.request(key, object(), 'diagnostic coordinate')
    tail = registry.request('tail', object())
    error = ValueError('language-specific diagnostic')
    def fail(entry):
        assert entry is broken
        raise error
    with pytest.raises(ValueError) as first:
        registry.drain(fail)
    assert first.value is error
    assert registry.entries[key] is broken
    assert broken.state is InstanceState.FAILED and broken.failure is error
    assert tail.state is InstanceState.QUEUED
    actions = {
        'get': lambda: registry.get(key),
        'request-same': lambda: registry.request(key, object()),
        'request-new': lambda: registry.request('new', object()),
        'drain': lambda: registry.drain(lambda entry: pytest.fail('failed session resumed')),
        'view': lambda: registry.value_view('owner')[('i32',)],
    }
    with pytest.raises(ValueError) as repeated:
        actions[operation]()
    assert repeated.value is error and len(registry) == 2
    assert tail.state is InstanceState.QUEUED


def test_reentrant_drain_fails_instead_of_executing_one_body_twice():
    registry = InstanceRegistry()
    entry = registry.request('a', object())
    with pytest.raises(RuntimeError, match='cannot be drained recursively') as failure:
        registry.drain(lambda current: registry.drain(lambda nested: None))
    assert entry.state is InstanceState.FAILED and entry.failure is failure.value
