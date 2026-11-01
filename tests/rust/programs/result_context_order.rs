#![no_std]
enum Maybe<T>{Empty,Some(T)}
fn empty<T>()->Maybe<T>{Maybe::<T>::Empty}
fn never<T>(ignored:u64)->Maybe<T>{while true{} empty()}
fn combine(first:Maybe<u64>,second:Maybe<u64>)->u64{0}
fn early()->u64{combine(return 0xfedcba9876543210,never(0))}
pub extern "C" fn answer()->u64{
    let mut order=0u64;
    let value=combine({order=order*10+1;empty()},{order=order*10+2;empty()});
    if order==12 && value==0{early()}else{0}
}
