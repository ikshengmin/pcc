#![no_std]
enum Maybe<T>{Empty,Some(T)}
fn next<T>(order:&mut u64)->Maybe<T>{*order=*order*10+1;Maybe::<T>::Empty}
fn combine(first:Maybe<u64>,second:Maybe<u64>)->u64{0}
pub extern "C" fn answer()->u64{
    let mut order=0u64;
    let result=combine(next(&mut order),{order=order*10+2;next(&mut order)});
    if order==121 && result==0{0xfedcba9876543210}else{0}
}
