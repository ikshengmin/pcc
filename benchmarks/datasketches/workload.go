// Licensed under the Apache License, Version 2.0 (PCC benchmark driver; original).
//
// The shared DataSketches workload, Go arm: for N keys 0..N-1, HLL_8 with
// lg_k 12, an update Theta sketch with lg_k 12 and KLL<float64> with k 200.
// Each family times only its update loop and prints one JSON line with the
// result (estimate or median) so arms can be compared for equal work.
package main

import (
	"errors"
	"fmt"
	"os"
	"strconv"
	"time"

	"github.com/apache/datasketches-go/common"
	"github.com/apache/datasketches-go/hll"
	"github.com/apache/datasketches-go/kll"
	"github.com/apache/datasketches-go/theta"
)

func report(family string, n uint64, seconds float64, result float64) {
	fmt.Printf("{\"family\":\"%s\",\"n\":%d,\"seconds\":%.9f,\"result\":%s}\n",
		family, n, seconds, strconv.FormatFloat(result, 'g', 17, 64))
}

func check(err error) {
	if err != nil {
		panic(err)
	}
}

func main() {
	n := uint64(1000000)
	if len(os.Args) > 1 {
		parsed, err := strconv.ParseUint(os.Args[1], 10, 64)
		check(err)
		n = parsed
	}

	sketch, err := hll.NewHllSketch(12, hll.TgtHllTypeHll8)
	check(err)
	start := time.Now()
	for i := uint64(0); i < n; i++ {
		check(sketch.UpdateUInt64(i))
	}
	elapsed := time.Since(start).Seconds()
	estimate, err := sketch.GetEstimate()
	check(err)
	report("hll8_lg12", n, elapsed, estimate)

	update, err := theta.NewQuickSelectUpdateSketch(theta.WithUpdateSketchLgK(12))
	check(err)
	start = time.Now()
	for i := uint64(0); i < n; i++ {
		// A screened hash or a repeated key is an update outcome, not a failure.
		if err := update.UpdateUint64(i); err != nil && !errors.Is(err, theta.ErrHashExceedsTheta) &&
			!errors.Is(err, theta.ErrDuplicateKey) {
			panic(err)
		}
	}
	report("theta_lg12", n, time.Since(start).Seconds(), update.Estimate())

	quantiles, err := kll.NewKllItemsSketch[float64](200, 8, common.ItemSketchDoubleComparator(false),
		common.ItemSketchDoubleSerDe{})
	check(err)
	start = time.Now()
	for i := uint64(0); i < n; i++ {
		quantiles.Update(float64(i))
	}
	elapsed = time.Since(start).Seconds()
	median, err := quantiles.GetQuantile(0.5, true)
	check(err)
	report("kll_double_k200", n, elapsed, median)
}
