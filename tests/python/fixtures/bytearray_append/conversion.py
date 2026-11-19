# Mutable Sequence Types: bytearray accepts index integers in range(256).
class Index:
    def __index__(self):
        return 72


class Huge:
    def __index__(self):
        return 1 << 80


class Raised:
    def __index__(self):
        raise OverflowError('from index')


def run():
    target = bytearray()
    target.append(True)
    target.append(Index())
    for value in (-1, 256, 1 << 80, Huge()):
        try:
            target.append(value)
        except ValueError:
            print('range')
    try:
        target.append(Raised())
    except OverflowError:
        print('callback')
    try:
        target.append(1.5)
    except TypeError:
        print('type')
    print(bytes(target))


run()
