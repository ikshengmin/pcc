"""Exact observable ordering for a reference-counted acyclic weakref target."""
import sys
import threading
import weakref

arm = sys.argv[1]
rescue = arm == "rescue"
cross_thread = arm.startswith("thread-")
go = threading.Event()
observed = threading.Event()
check_again = threading.Event()
events = []
hold = None


class Subject:
    def __del__(self):
        global hold
        events.append(f"del:{int(watch() is self)}")
        if rescue:
            hold = self
        if cross_thread:
            go.set()
            assert observed.wait(5)



def read_weak():
    global hold
    assert go.wait(5)
    target = watch()
    events.append(f"thread:{int(target is not None)}")
    if arm in ("thread-publish", "thread-publish-clear"):
        hold = target
    if arm == "thread-publish-clear":
        hold = None
    if arm != "thread-hold":
        del target
    observed.set()
    assert check_again.wait(5)
    events.append(f"after:{int(watch() is not None)}")
    # Save the observed prefix before dropping a reference carried across
    # the finalizer, which can itself deliver the eventual weak callback.
    if arm == "thread-hold":
        saved_events[:] = events


subject = Subject()
watch = weakref.ref(subject, lambda wr: events.append(f"weak:{int(wr() is None)}"))
saved_events = []
if cross_thread:
    worker = threading.Thread(target=read_weak)
    worker.start()
del subject
events.append(f"live:{int(watch() is not None)}")
if cross_thread:
    check_again.set()
    worker.join(5)
    assert not worker.is_alive()
if not saved_events:
    saved_events = events[:]
print(" ".join(saved_events))
