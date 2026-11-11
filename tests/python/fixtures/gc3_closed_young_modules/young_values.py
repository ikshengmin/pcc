"""A live sibling-global scalar must survive allocation in another module."""

class Box:
    pass


def make_saved(value):
    return str(value) + '-root'


saved = make_saved(2468)
holder = Box()
holder.value = saved


def exercise():
    values = []
    mapping = {}
    box = Box()
    total = 0
    for index in range(2048):
        value = str(index)
        box.value = value
        mapping['value'] = value
        if index % 127 == 0:
            values.append(value)
        if box.value != value or mapping['value'] != value:
            raise ValueError('owner lost')
        if index % 43 == 0:
            try:
                raise ValueError(value)
            except ValueError as problem:
                if str(problem) != value:
                    raise ValueError('exception lost')
        total += len(value)
    if saved != '2468-root' or holder.value != saved:
        raise ValueError('sibling global lost')
    return total + len(values)
