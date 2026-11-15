from cyclecase import Fault, Cell, observed


def main():
    assert observed == (True, True, False, 42)
    assert isinstance(Fault('message'), Exception)
    assert Cell(7).value == 7
    print('CLASS_CYCLE_OK')


main()
