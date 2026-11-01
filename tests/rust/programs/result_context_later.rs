#![no_std]
enum Maybe<T>{Empty,Some(T)}
fn empty<T>()->Maybe<T>{Maybe::<T>::Empty}
fn tag(value:Maybe<u64>)->u64{match value{Maybe::Empty=>1,Maybe::Some(x)=>x}}
fn choose(flag:bool)->Maybe<u64>{if flag{empty()}else{Maybe::<u64>::Some(5)}}
pub extern "C" fn answer()->u64{
    let mut late=empty();
    late=Maybe::<u64>::Some(0xfedcba9876543210);
    let argument=empty();
    if tag(argument)+tag(choose(true))+tag(choose(false))==7{tag(late)}else{0}
}
