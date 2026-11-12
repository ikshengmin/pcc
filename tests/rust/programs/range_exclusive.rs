pub fn answer() -> u64 {
    let mut sum = 0u64;
    let mut count = 0u64;
    for i in 0..10u64 {
        sum += i;
        count += 1;
    }
    for _ in 7u64..7 {
        count += 100;
    }
    if sum == 45 && count == 10 { 0xfedcba9876543210u64 } else { 0 }
}
