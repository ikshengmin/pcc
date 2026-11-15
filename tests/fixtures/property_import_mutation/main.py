"""Property data descriptors: Python data model, Implementing Descriptors.

Imported method metadata cannot prove that a live property lacks a mutator.
Run unchanged with CPython and the native compiler, including all collectors.
"""
from owner import Cell, ReadOnly


class Child(Cell):
    pass


def typed_mutation(value: Cell):
    value.value = 11
    assert value.value == 11 and value.writes == 2
    del value.value
    assert value.value is None and value.writes == 5


def main():
    cell = Cell(1)
    cell.value = 2
    assert cell.value == 2 and cell.writes == 2
    del cell.value
    assert cell.value is None and cell.writes == 5
    print('imported', cell.writes)
    typed_mutation(Cell(3))
    typed_mutation(Child(4))
    print('typed and inherited')
    readonly = ReadOnly()
    try:
        readonly.value = 9
    except AttributeError:
        print('readonly set')
    else:
        raise AssertionError('read-only property accepted a store')
    try:
        del readonly.value
    except AttributeError:
        print('readonly delete')
    else:
        raise AssertionError('read-only property accepted deletion')
    assert readonly.value == 7
    print('PROPERTY_IMPORT_MUTATION_OK')


main()
