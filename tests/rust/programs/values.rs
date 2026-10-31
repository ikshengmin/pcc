#![no_std]
fn wide(value: i64) -> i64 { value + 1 }
fn wrap(value: u32) -> u32 { value + 1 }
fn low(value: i32) -> i32 { value - 1 }
pub extern "C" fn answer() -> i32 {
    let inferred = 40;
    let changed = { let inferred = wide(inferred); inferred + 1 };
    let maximum = 0xffff_ffffu32;
    let minimum = -2147483648i32;
    if changed == 42i64 && wrap(maximum) == 0u32 && low(minimum) == 2147483647i32 {
        changed as i32
    } else { 2 }
}
