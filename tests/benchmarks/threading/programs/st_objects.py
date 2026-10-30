import sys
import time


class Vec:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def add(self, other):
        return Vec(self.x + other.x, self.y + other.y)

    def dot(self, other):
        return self.x * other.x + self.y * other.y


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000000
    t0 = time.perf_counter()
    acc = Vec(0, 0)
    s = 0
    for i in range(n):
        v = Vec(i, i + 1)
        acc = acc.add(v)
        s += v.dot(acc) % 7
    t1 = time.perf_counter()
    print("objects", acc.x, acc.y, s, round(t1 - t0, 4))


main()
