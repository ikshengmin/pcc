#![no_std]
fn identity(value: (i64, (bool, i64))) -> (i64, (bool, i64)) { value }
pub extern "C" fn answer() -> i64 {
    let mut first: (i64, (bool, i64)) = (20, (true, 22));
    let second = identity(first);
    let third = first;
    first.0 = 99;
    if third.1.0 && first.0 == 99 { second.0 + third.1.1 } else { -1 }
}
