#![no_std]
fn identity(value: i32) -> i32 { value }
pub extern "C" fn answer() -> i32 {
    let minimum = -((2147483648));
    let condition = (1 < 2) == (2 < 3);
    let called = (identity)(42);
    if (condition) && (minimum == -2147483648) { (called) } else { 5 }
}
