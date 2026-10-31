#![no_std]
fn recurse(n: i32) -> i32 {
    if n <= 1 { return 1; }
    n * recurse(n - 1)
}
pub extern "C" fn answer() -> i32 {
    let mut count = 0;
    false && { count += 1; true };
    true || { count += 10; false };
    true && { count += 2; true };
    false || { count += 4; false };
    if count == 6 && recurse(4) == 24 { 42 } else { 3 }
}
