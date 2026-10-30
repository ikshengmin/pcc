import sys
import time


def fib(n):
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    t0 = time.perf_counter()
    r = fib(n)
    t1 = time.perf_counter()
    print("fib", r, round(t1 - t0, 4))


main()
