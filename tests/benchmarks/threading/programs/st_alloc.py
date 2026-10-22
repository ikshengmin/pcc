import sys
import time


def work(n):
    d = {}
    for i in range(n):
        d[i] = (i, str(i))
    total = 0
    for k in d:
        total += d[k][0] + len(d[k][1])
    items = [x for x in range(n) if x % 3 == 0]
    return total + len(items)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200000
    t0 = time.perf_counter()
    r = 0
    for _ in range(10):
        r += work(n)
    t1 = time.perf_counter()
    print("alloc", r, round(t1 - t0, 4))


main()
