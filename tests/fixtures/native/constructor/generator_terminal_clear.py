"""Ordinary terminal/frame lifetime cases, shared unchanged with CPython.

Contract: project intent Python finalizers/weakrefs/resurrection; Python language
reference 6.2.9 (yield expressions), data model 3.3.1 (__del__). No benchmark path.
"""
import gc
import sys
import weakref

references = []
finalized = []
weak_events = []
resurrected = []
violations = []


def weak_gone(ref):
    weak_events.append(ref)


class Witness:
    def __init__(self, name):
        self.name = name
        self.token = 73
        references.append(weakref.ref(self, weak_gone))

    def __del__(self):
        finalized.append(self.name)
        if self.token != 73:
            violations.append(self.name)
        if self.name == "resurrect":
            resurrected.append(self)
        gc.collect()


def primitive_frame():
    a0 = 0
    a1 = 1
    a2 = 2
    a3 = 3
    a4 = 4
    a5 = 5
    a6 = 6
    a7 = 7
    a8 = 8
    a9 = 9
    a10 = 10
    a11 = 11
    a12 = 12
    a13 = 13
    a14 = 14
    a15 = 15
    a16 = 16
    yield 17
    return a0 + a1 + a2 + a3 + a4 + a5 + a6 + a7 + a8 + a9 + a10 + a11 + a12 + a13 + a14 + a15 + a16


def heap_frame(case, payload):
    first = [Witness("first")]
    second = [Witness("resurrect")]
    yield 1
    first = None
    second = None
    if case == "error":
        raise ValueError("terminal-original")
    return payload


def collect():
    gc.collect()
    gc.collect()


def main():
    case = sys.argv[1]
    if case == "primitive":
        generator = primitive_frame()
        assert next(generator) == 17
        try:
            next(generator)
        except StopIteration as error:
            assert error.value == 136
        else:
            raise AssertionError("missing return")
    else:
        payload = ["identity"]
        generator = heap_frame(case, payload)
        assert next(generator) == 1
        assert references[0]() is not None and references[1]() is not None
        if case == "close":
            generator.close()
        else:
            try:
                next(generator)
            except StopIteration as error:
                assert case == "return" and error.value is payload
            except ValueError as error:
                assert case == "error" and str(error) == "terminal-original"
            else:
                raise AssertionError("missing terminal exception")
        collect()
        assert sorted(finalized) == ["first", "resurrect"]
        assert violations == []
        assert references[0]() is None
        assert weak_events.count(references[0]) == 1
        assert len(resurrected) == 1
        assert references[1]() is resurrected[0]
        assert weak_events.count(references[1]) == 0
        resurrected.clear()
        collect()
        assert references[1]() is None
        assert weak_events.count(references[1]) == 1
        assert sorted(finalized) == ["first", "resurrect"]
    try:
        next(generator)
    except StopIteration:
        pass
    else:
        raise AssertionError("completed generator resumed")
    print("TERMINAL_CLEAR_OK:" + case)


main()
