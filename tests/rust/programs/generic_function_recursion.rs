#![no_std]
fn descend<T>(n:i64,value:T)->T {
    if n==0 {value} else {descend::<T>(n-1,value)}
}
fn left<T>(n:i64,value:T)->T {
    if n==0 {value} else {right::<T>(n-1,value)}
}
fn right<T>(n:i64,value:T)->T {
    if n==0 {value} else {left::<T>(n-1,value)}
}
struct Value{bits:u64}
fn answer()->u64 {
    let value=descend::<Value>(3,Value{bits:0xfedcba9876543210u64});
    left::<Value>(4,value).bits
}
