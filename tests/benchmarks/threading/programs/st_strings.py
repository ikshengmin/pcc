import sys
import time


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300000
    t0 = time.perf_counter()
    total = 0
    for i in range(n):
        s = "item-" + str(i)
        parts = s.split("-")
        total += len("|".join(parts * 3))
        if s.startswith("item-1"):
            total += 1
    t1 = time.perf_counter()
    print("strings", total, round(t1 - t0, 4))


main()
