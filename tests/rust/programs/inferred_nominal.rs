#![no_std]
struct Wrap<T>{value:T}
fn wrap<T>(value:T)->Wrap<T>{Wrap::<T>{value:value}}
fn unwrap<T>(value:Wrap<T>)->T{value.value}
pub extern "C" fn answer()->u64{
    let wrapped=wrap(0xfedcba9876543210);
    let a:u64=unwrap(wrapped);
    let b:u64=wrap(0xfedcba9876543210).value;
    if a==b {a}else{0}
}
