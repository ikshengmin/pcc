#![no_std]
fn pack<T,U>(a:T,b:U)->(T,U){(a,b)}
fn second<T,U>(pair:(T,U))->U{pair.1}
fn id<T>(x:T)->T{x}
pub extern "C" fn answer()->u64{
    let pair=pack(-7i64,id(0xfedcba9876543210));
    second(pair)
}
