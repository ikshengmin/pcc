pub fn answer() -> u64 {
    let mut count = 0u64;
    let mut last = 0u32;
    for value in 4294967290u32..=4294967295 {
        count += 1;
        last = value;
    }
    let mut empty = 0u64;
    for _ in 3u32..=2 {
        empty += 1;
    }
    let mut single = 0u64;
    for value in 4294967295u32..=4294967295 {
        single += value as u64;
    }
    if count == 6 && last == 4294967295 && empty == 0 && single == 4294967295 { 0xfedcba9876543210u64 } else { 0 }
}
