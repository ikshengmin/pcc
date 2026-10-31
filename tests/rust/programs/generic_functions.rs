#![no_std]
fn identity<T>(value:T)->T { value }
fn first<T,U>(a:T,b:U)->T { let discarded=b; a }
fn answer()->u64 {
    let signed=identity::<i64>(-17);
    let unit=identity::<()>(());
    first::<u64,()>(identity::<u64>(0xfedcba9876543210u64),unit)
}
