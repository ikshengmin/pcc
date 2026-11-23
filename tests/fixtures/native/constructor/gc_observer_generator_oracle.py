import gc
import threading

events = []

def values(label):
    try:
        yield 1
    finally:
        events.append(label)

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
gc.collect()
assert events == ["suspended"], events
gc.collect()
assert events == ["suspended"], events
print("suspended closed once; unstarted not closed")
