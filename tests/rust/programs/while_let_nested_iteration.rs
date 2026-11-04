pub fn answer() -> u64 {
    let mut sum = 0u64;
    for value in [20u64, 22] {
        let mut phase = 0u64;
        while let true = phase < 2 {
            phase += 1;
            if phase == 1 { continue; }
            sum += value;
        }
    }
    if sum == 42 { 0xfedcba9876543210u64 } else { 0 }
}
