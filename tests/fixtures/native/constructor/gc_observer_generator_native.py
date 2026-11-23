import gc
import threading
from pcc.extern import extern, c_int64
metric = extern("native_generator_counter", (c_int64,), c_int64)

events = []
observed = []

def values(label):
    try:
        yield 1
    finally:
        events.append(label)
        observed.append((metric(1), metric(2), metric(3), metric(4)))

def create_cycles():
    started = values("suspended")
    assert next(started) == 1
    never_started = values("not started")
    chain = [started, never_started]
    chain.append(chain)

gc.disable()
producer = threading.Thread(target=create_cycles)
producer.start()
producer.join()
assert events == []
backend = metric(0)
before = metric(1)
assert metric(3) == 0 and metric(4) == 0
gc.collect()
committed = metric(1)
print("mark", backend, before, committed, metric(2), observed)
if 1 <= backend <= 3:
    assert len(observed) == 1
    at_commit, at_hint, classes, weak = observed[0]
    assert before < at_commit <= committed and at_hint != at_commit
    assert classes == weak == 0
assert events == ["suspended"], events
gc.collect()
if 1 <= backend <= 3:
    assert metric(1) >= committed + 1 and metric(2) == metric(1)
assert events == ["suspended"], events
print("suspended closed once; unstarted not closed")
