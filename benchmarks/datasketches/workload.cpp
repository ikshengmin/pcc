// Licensed under the Apache License, Version 2.0 (PCC benchmark driver; original).
//
// The shared DataSketches workload, C++ arm: for N keys 0..N-1, HLL_8 with
// lg_k 12, an update Theta sketch with lg_k 12 and KLL<double> with k 200.
// Each family times only its update loop and prints one JSON line with the
// result (estimate or median) so arms can be compared for equal work.
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>

#include "hll.hpp"
#include "kll_sketch.hpp"
#include "theta_sketch.hpp"

namespace {

template <typename Body>
double seconds(Body body) {
  const auto start = std::chrono::steady_clock::now();
  body();
  return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
}

void report(const char* family, uint64_t n, double elapsed, double result) {
  std::printf("{\"family\":\"%s\",\"n\":%llu,\"seconds\":%.9f,\"result\":%.17g}\n", family,
              static_cast<unsigned long long>(n), elapsed, result);
}

}  // namespace

int main(int argc, char** argv) {
  const uint64_t n = argc > 1 ? std::strtoull(argv[1], nullptr, 10) : 1000000;
  datasketches::hll_sketch hll(12, datasketches::HLL_8);
  const double hll_time = seconds([&] { for (uint64_t i = 0; i < n; ++i) hll.update(i); });
  report("hll8_lg12", n, hll_time, hll.get_estimate());

  auto theta = datasketches::update_theta_sketch::builder().set_lg_k(12).build();
  const double theta_time = seconds([&] { for (uint64_t i = 0; i < n; ++i) theta.update(i); });
  report("theta_lg12", n, theta_time, theta.get_estimate());

  datasketches::kll_sketch<double> kll(200);
  const double kll_time = seconds([&] { for (uint64_t i = 0; i < n; ++i) kll.update(static_cast<double>(i)); });
  report("kll_double_k200", n, kll_time, kll.get_quantile(0.5));
  return 0;
}
