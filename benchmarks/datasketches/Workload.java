// Licensed under the Apache License, Version 2.0 (PCC benchmark driver; original).
//
// The shared DataSketches workload, Java arm: for N keys 0..N-1, HLL_8 with
// lg_k 12, an update Theta sketch with lg_k 12 (4096 nominal entries) and
// KLL<double> with k 200. Each family times only its update loop and prints
// one JSON line with the result so arms can be compared for equal work.
import java.util.Locale;

import org.apache.datasketches.hll.HllSketch;
import org.apache.datasketches.hll.TgtHllType;
import org.apache.datasketches.kll.KllDoublesSketch;
import org.apache.datasketches.theta.UpdatableThetaSketch;

public final class Workload {
  private static void report(final String family, final long n, final double seconds, final double result) {
    System.out.println(String.format(Locale.ROOT, "{\"family\":\"%s\",\"n\":%d,\"seconds\":%.9f,\"result\":%s}",
        family, n, seconds, Double.toString(result)));
  }

  public static void main(final String[] args) {
    final long n = args.length > 0 ? Long.parseLong(args[0]) : 1_000_000L;

    final HllSketch hll = new HllSketch(12, TgtHllType.HLL_8);
    long start = System.nanoTime();
    for (long i = 0; i < n; i++) { hll.update(i); }
    report("hll8_lg12", n, (System.nanoTime() - start) / 1e9, hll.getEstimate());

    final UpdatableThetaSketch theta = UpdatableThetaSketch.builder().setNominalEntries(1 << 12).build();
    start = System.nanoTime();
    for (long i = 0; i < n; i++) { theta.update(i); }
    report("theta_lg12", n, (System.nanoTime() - start) / 1e9, theta.getEstimate());

    final KllDoublesSketch kll = KllDoublesSketch.newHeapInstance(200);
    start = System.nanoTime();
    for (long i = 0; i < n; i++) { kll.update((double) i); }
    final double seconds = (System.nanoTime() - start) / 1e9;
    report("kll_double_k200", n, seconds, kll.getQuantile(0.5));
  }
}
