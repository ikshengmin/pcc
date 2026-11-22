"""Exact observable ordering for a reference-counted acyclic weakref target."""
import sys
import weakref

rescue = sys.argv[1] == "rescue"
events = []
hold = None


class Subject:
    def __del__(self):
        global hold
        events.append(f"del:{int(watch() is self)}")
        if rescue:
            hold = self


subject = Subject()
watch = weakref.ref(subject, lambda wr: events.append(f"weak:{int(wr() is None)}"))
del subject
events.append(f"live:{int(watch() is not None)}")
saved_events = events[:]
print(" ".join(saved_events))
