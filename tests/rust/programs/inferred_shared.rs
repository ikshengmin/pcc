#![no_std]
struct Payload{x:i32}
struct Wrap<T>{value:T,code:u64}
fn read<T>(p:&Wrap<T>)->u64{p.code}
pub extern "C" fn answer()->u64{
    let value=Wrap::<Payload>{value:Payload{x:7},code:0xfedcba9876543210};
    let answer=read(&value);
    let payload=value.value;
    if payload.x==7 {answer}else{0}
}
