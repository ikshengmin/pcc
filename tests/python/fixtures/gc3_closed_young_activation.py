"""Import-free scalar allocation with ordinary owners and exception roots.

Python reference:
https://docs.python.org/3.15/reference/datamodel.html#objects-values-and-types
https://docs.python.org/3.15/reference/compound_stmts.html#the-try-statement
Keep this source identical for native execution and the CPython oracle;
importing telemetry would revoke admission.
"""


class Box:
    pass


def exercise():
    box = Box()
    values = []
    mapping = {}
    stringify = str
    total = 0
    for index in range(2048):
        value = stringify(index)
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
    return total + len(values)


# Open the owned stream before allocating. The child stays alive at READY
# while its parent reads counters, without raw/FFI imports or an input() stub.
stream = open('/dev/stdin', 'rb')
print(exercise())
print('READY')
stream.read(1)
stream.close()
