"""Dynamic-call failure restores its temporary exception pin before returning."""
import pytest

from tests.python.owned_regression_support import assert_owned_program, explicit_owned_runtime


PROGRAM = r'''import gc
import weakref

class CallbackError(Exception):
    pass

watched = []
callbacks = [0, 0, 0, 0]

def disposed(ref):
    if ref() is not None:
        callbacks[3] += 1
    elif ref is watched[0]:
        callbacks[0] += 1
    elif len(watched) == 2 and ref is watched[1]:
        callbacks[1] += 1
    else:
        callbacks[2] += 1

def throw_fresh():
    error = CallbackError("crossed return")
    watched.append(weakref.ref(error, disposed))
    raise error

routes = [throw_fresh]

def catch_direct():
    try:
        throw_fresh()
    except CallbackError:
        return True
    return False

def catch_indirect():
    try:
        routes[0]()
    except CallbackError:
        return True
    return False

def main():
    if INDIRECT_FIRST:
        indirect = catch_indirect()
        direct = catch_direct()
        direct_index = 1
        indirect_index = 0
    else:
        direct = catch_direct()
        indirect = catch_indirect()
        direct_index = 0
        indirect_index = 1
    for index in range(4):
        gc.collect()
    print("outside-catch", direct, indirect, "weakrefs", len(watched))
    assert direct and indirect and len(watched) == 2
    direct_dead = watched[direct_index]() is None
    indirect_dead = watched[indirect_index]() is None
    print("cleared", direct_dead, indirect_dead)
    print("callbacks", callbacks)
    assert direct_dead and indirect_dead and callbacks == [1, 1, 0, 0]

main()
'''


@pytest.mark.integration
@pytest.mark.xdist_group("dynamic_exception_pin_native")
@pytest.mark.parametrize("indirect_first", [False, True], ids=["direct-first", "indirect-first"])
@pytest.mark.parametrize("python_program_compiler", ("pcc0", "pcc1"), indirect=True)
def test_dynamic_exception_is_collected(
    indirect_first, python_program_compiler, request, explicit_owned_runtime, tmp_path, capfd,
):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(
        PROGRAM.replace("INDIRECT_FIRST", str(indirect_first)),
        "outside-catch True True weakrefs 2\ncleared True True\ncallbacks [1, 1, 0, 0]\n",
        tmp_path, python_program_compiler, mode, explicit_owned_runtime, capfd,
    )
