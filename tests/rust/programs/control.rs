#![no_std]
fn sum(limit: i32) -> i32 {
    let mut n = 0;
    let mut result = 0;
    while n < limit {
        n += 1;
        if n == 3 { continue; }
        if n == 9 { break; }
        result += n;
    }
    result
}
pub extern "C" fn answer() -> i32 {
    let result = sum(12);
    if result == 33 { 42 } else { 1 }
}
