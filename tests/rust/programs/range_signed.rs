pub fn answer() -> u64 {
    let mut sum = 0i32;
    for i in -3i32..3 {
        sum += i;
    }
    let mut low = 0i64;
    for i in -2i64..=-1 {
        low += i;
    }
    let mut skipped = 0u64;
    for _ in 5i32..-5 {
        skipped += 1;
    }
    if sum == -3 && low == -3 && skipped == 0 { 0xfedcba9876543210u64 } else { 0 }
}
