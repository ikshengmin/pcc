#![no_std]
enum Maybe<T>{None,Some(T)}
struct Value{bits:u64}
fn unwrap_or<T>(choice:Maybe<T>,default:T)->T {
    match choice { Maybe::None=>default, Maybe::Some(value)=>value }
}
fn answer()->u64 {
    let missing=unwrap_or::<Value>(Maybe::<Value>::None,Value{bits:7});
    let present=unwrap_or::<Value>(Maybe::<Value>::Some(Value{bits:0xfedcba9876543210u64}),missing);
    present.bits
}
