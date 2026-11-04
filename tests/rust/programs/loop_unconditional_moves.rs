struct R { x: u64 }
fn take(value: R) -> u64 { value.x }
pub fn answer() -> u64 {
    let mut value = R { x: 14 };
    let mut sum = 0u64;
    loop {
        sum += take(value);
        if sum == 42 { break; }
        value = R { x: 14 };
    }
    if sum == 42 { 0xfedcba9876543210u64 } else { 0 }
}
