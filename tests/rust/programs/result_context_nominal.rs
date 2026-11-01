#![no_std]
enum Maybe<T>{Empty,Some(T)}
fn empty<T>()->Maybe<T>{Maybe::<T>::Empty}
fn typed()->Maybe<u64>{empty()}
fn tag(value:Maybe<u64>)->u64{match value{Maybe::Empty=>1,Maybe::Some(x)=>x}}
pub extern "C" fn answer()->u64{
    let local:Maybe<u64>=empty();
    if tag(local)+tag(typed())+tag(empty())==3{0xfedcba9876543210}else{0}
}
