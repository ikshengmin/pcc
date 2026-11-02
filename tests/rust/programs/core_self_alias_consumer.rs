extern crate self as core;
pub struct Data{pub n:u64}
fn base()->u64{40}
mod consumer {
    pub fn read(x:&core::Data)->u64{core::base()+x.n}
}
mod shadow {
    mod core {pub fn base()->u64{900}}
    pub fn local()->u64{core::base()}
    pub fn original()->u64{crate::core::base()}
}
pub fn answer()->u64 {
    let data=Data{n:2};
    if consumer::read(&data)==42 && shadow::local()==900 && shadow::original()==40 {
        0xfedcba9876543210u64
    } else {0}
}
