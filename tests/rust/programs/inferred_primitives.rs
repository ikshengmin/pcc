#![no_std]
fn id<T>(value:T)->T{value}
pub extern "C" fn answer()->u64{
    let wide:u64=id(0xfedcba9876543210);
    let n=id(9);
    if id(true) && n==9 && id(12u32)==12u32 {wide}else{0}
}
