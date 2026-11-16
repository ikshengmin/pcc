# Licensed under the Apache License, Version 2.0 (PCC benchmark driver; original).
#
# The shared DataSketches workload, Python-package arm: for N keys 0..N-1,
# HLL_8 with lg_k 12, an update Theta sketch with lg_k 12 and KLL<double> with
# k 200, each through the `datasketches` package from a Python loop. Each
# family times only its update loop and prints one JSON line with the result.
import json
import sys
import time

from datasketches import hll_sketch, kll_doubles_sketch, tgt_hll_type, update_theta_sketch


def report(family, n, seconds, result):
    print(json.dumps({"family": family, "n": n, "seconds": round(seconds, 9), "result": result},
                     separators=(",", ":")))


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000

    hll = hll_sketch(12, tgt_hll_type.HLL_8)
    start = time.perf_counter()
    for i in range(n):
        hll.update(i)
    report("hll8_lg12", n, time.perf_counter() - start, hll.get_estimate())

    theta = update_theta_sketch(12)
    start = time.perf_counter()
    for i in range(n):
        theta.update(i)
    report("theta_lg12", n, time.perf_counter() - start, theta.get_estimate())

    kll = kll_doubles_sketch(200)
    start = time.perf_counter()
    for i in range(n):
        kll.update(float(i))
    seconds = time.perf_counter() - start
    report("kll_double_k200", n, seconds, kll.get_quantile(0.5))


if __name__ == "__main__":
    main()
