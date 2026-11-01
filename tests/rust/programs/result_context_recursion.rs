#![no_std]
enum Maybe<T>{Empty,Some(T)}
fn empty<T>()->Maybe<T>{Maybe::<T>::Empty}
fn outer<U>()->Maybe<U>{empty()}
fn recurse<T>(count:u64)->Maybe<T>{if count==0{outer()}else{recurse(count-1)}}
fn tag(value:Maybe<u64>)->u64{match value{Maybe::Empty=>1,Maybe::Some(x)=>x}}
fn small(value:Maybe<i32>)->u64{match value{Maybe::Empty=>2,Maybe::Some(x)=>x as u64}}
pub extern "C" fn answer()->u64{
    if tag(recurse(3))+small(outer())+tag(empty::<u64>())==4{0xfedcba9876543210}else{0}
}
