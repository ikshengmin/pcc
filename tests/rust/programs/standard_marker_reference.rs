#![no_std]
fn read<T: core::marker::Copy>(value: &T) -> T { *value }
fn inspect<T: ?Sized>(value: &T) -> u64 { 42 }
fn forward<U: ?Sized>(value: &U) -> u64 { inspect(value) }
pub extern "C" fn answer() -> u64 {
    let value = 21u64;
    if read(&value) + read(&value) == forward(&value) { 0xfedcba9876543210 } else { 0 }
}
