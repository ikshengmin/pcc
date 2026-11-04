pub fn answer() -> u64 {
    let mut index = 0u64;
    let mut sum = 0u64;
    loop {
        index += 1;
        if index == 8 { break; }
        if (index & 1) == 0 { continue; }
        sum += index;
    }
    if sum == 16 && index == 8 { 0xfedcba9876543210u64 } else { 0 }
}
