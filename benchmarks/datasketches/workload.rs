// Licensed under the Apache License, Version 2.0 (PCC benchmark driver; original).
//
// The shared DataSketches workload, Rust arm: for N keys 0..N-1, HLL_8 with
// lg_k 12, an update Theta sketch with lg_k 12 and KLL<f64> with k 200. Each
// family times only its update loop and prints one JSON line with the result
// (estimate or median) so arms can be compared for equal work.
use std::time::Instant;

use datasketches::common::SearchCriteria;
use datasketches::hll::{HllSketch, HllType};
use datasketches::kll::{KllFloat, KllSketch};
use datasketches::theta::ThetaSketchBuilder;

fn report(family: &str, n: u64, seconds: f64, result: f64) {
    println!("{{\"family\":\"{family}\",\"n\":{n},\"seconds\":{seconds:.9},\"result\":{result:?}}}");
}

fn main() {
    let n: u64 = std::env::args().nth(1).map_or(1_000_000, |text| text.parse().expect("N"));

    let mut hll = HllSketch::new(12, HllType::Hll8).expect("hll");
    let start = Instant::now();
    for i in 0..n {
        hll.update(i);
    }
    report("hll8_lg12", n, start.elapsed().as_secs_f64(), hll.estimate());

    let mut theta = ThetaSketchBuilder::default().lg_k(12).build().expect("theta");
    let start = Instant::now();
    for i in 0..n {
        theta.update(i);
    }
    report("theta_lg12", n, start.elapsed().as_secs_f64(), theta.estimate());

    // KllFloat is the crate's totally ordered (NaN-rejecting) f64.
    let mut kll = KllSketch::<KllFloat<f64>>::new(200).expect("kll");
    let start = Instant::now();
    for i in 0..n {
        kll.update(KllFloat::<f64>::new(i as f64).expect("finite"));
    }
    let median = kll.quantile(0.5, SearchCriteria::Inclusive).expect("quantile").expect("nonempty");
    report("kll_double_k200", n, start.elapsed().as_secs_f64(), *median);
}
