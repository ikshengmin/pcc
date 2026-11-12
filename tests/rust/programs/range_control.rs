pub fn answer() -> u64 {
    let mut total = 0u64;
    for outer in 0u64..5 {
        if outer == 1 {
            continue;
        }
        for inner in 0u64..=outer {
            if inner == 3 {
                break;
            }
            total += outer * 10 + inner;
        }
    }
    if total == 279 { 0xfedcba9876543210u64 } else { total }
}
