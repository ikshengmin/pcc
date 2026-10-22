import sys
import threading
import time


def worker(count, out, index):
    s = 0
    for _ in range(count):
        d = {}
        for i in range(20000):
            d[i] = str(i)
        s += len(d)
    out[index] = s


def main():
    n = int(sys.argv[1])
    jobs = int(sys.argv[2]) if len(sys.argv) > 2 else 48
    per = jobs // n
    out = [0] * n
    threads = [threading.Thread(target=worker, args=(per, out, i)) for i in range(n)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    t1 = time.perf_counter()
    print("mt_alloc", n, sum(out), round(t1 - t0, 4))


main()
