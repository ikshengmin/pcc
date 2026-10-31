#![no_std]
#[repr(C)]
struct Record { small: i32, wide: u64, signed: i64 }
fn read(a: &Record) -> u64 {
    let first = a;
    let second = a;
    if first.small == 20 && second.signed == -22 { second.wide } else { 1 }
}
#[no_mangle]
pub extern "C" fn inspect(a: &Record, b: &Record) -> u64 {
    let mut chosen = b;
    chosen = a;
    if read(chosen) == (if true { a } else { b }).wide && b.small == 22 {
        read(a)
    } else { 2 }
}
