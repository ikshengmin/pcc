fn collect() -> u64 {
    let mut sum = 0u64;
    loop {
        for value in [20u64, 22, 99] {
            loop {
                sum += value;
                break;
            }
            if value == 22 { break; }
        }
        break;
    }
    sum
}
fn return_from_array() -> u64 {
    loop {
        for value in [42u64, 99] {
            loop { return value; }
        }
    }
}
pub fn answer() -> u64 {
    if collect() == 42 && return_from_array() == 42 {
        0xfedcba9876543210u64
    } else { 0 }
}
