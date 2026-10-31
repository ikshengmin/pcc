#![no_std]
fn truncating(value: i64) -> i64 { value / 3 + value % 3 }
pub extern "C" fn answer() -> i32 {
    let high: u64 = 18446744073709551615u64;
    let negative: i32 = -1;
    let converted = negative as u64;
    let narrowed = high as i32;
    if truncating(-8) == -4 && converted == high && narrowed == negative && high > 0u64 {
        42
    } else { 4 }
}
