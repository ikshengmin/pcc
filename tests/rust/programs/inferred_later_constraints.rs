#![no_std]
fn id<T>(value:T)->T{value}
fn wide(value:i64)->i64{value}
fn result()->u64{id(0xfedcba9876543210)}
pub extern "C" fn answer()->u64{
    let n=id(4294967296);
    if wide(n)==4294967296i64 {result()}else{0}
}
