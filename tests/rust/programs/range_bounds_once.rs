pub fn answer() -> u64 {
    let mut n = 3u64;
    let mut iterations = 0u64;
    for _ in 0..n {
        n += 1;
        iterations += 1;
    }
    let mut start = 2u64;
    let mut seen = 0u64;
    for i in start..start + 3 {
        start += 10;
        seen += i;
    }
    if iterations == 3 && n == 6 && seen == 9 && start == 32 { 0xfedcba9876543210u64 } else { 0 }
}
