pub fn answer() -> u64 {
    let mut sum = 0u64;
    for value in [10u64, 12, 20] {
        let mut phase = 0u64;
        loop {
            phase += 1;
            if phase == 1 { continue; }
            sum += value;
            break;
        }
    }
    if sum == 42 { 0xfedcba9876543210u64 } else { 0 }
}
