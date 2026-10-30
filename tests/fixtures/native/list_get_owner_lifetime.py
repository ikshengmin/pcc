"""Draft native five-GC getter owner test. Compile with a threaded archive."""
import gc
import weakref

events = []
rescued = []

class Value:
    def __init__(self, name, rescue):
        self.name = name
        self.rescue = rescue
    def __del__(self):
        events.append(self.name)
        if self.rescue:
            rescued.append(self)
        gc.collect()

def read_owned(values: list, index: int):
    return values[index]

def main():
    value = Value("plain", False)
    reference = weakref.ref(value)
    values = [value]
    value = None
    copied = read_owned(values, -1)
    values[0] = None
    gc.collect()
    assert reference() is copied and events == []
    assert copied.name == "plain"
    copied = None
    gc.collect()
    assert reference() is None and events == ["plain"]
    events.clear()

    value = Value("rescue", True)
    reference = weakref.ref(value)
    values = [value]
    value = None
    copied = read_owned(values, 0)
    values[0] = None
    gc.collect()
    assert reference() is copied and events == []
    copied = None
    gc.collect()
    assert events == ["rescue"] and reference() is rescued[0]
    rescued.clear()
    gc.collect()
    assert reference() is None and events == ["rescue"]
    for index in (1, -2):
        try:
            read_owned(values, index)
        except IndexError:
            pass
        else:
            raise AssertionError("missing IndexError")
    print("LIST_GET_OWNER_LIFETIME_OK")

main()
