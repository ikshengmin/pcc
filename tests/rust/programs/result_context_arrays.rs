#![no_std]
fn empty<T>()->[T;0]{[]}
fn returned()->([u64;0],[bool;0]){(empty(),empty())}
fn consume(value:[i64;0])->usize{value.len()}
pub extern "C" fn answer()->u64{
    let local:[u64;0]=empty();
    let both=returned();
    if local.len()+both.0.len()+both.1.len()+consume(empty())==0usize{0xfedcba9876543210}else{0}
}
