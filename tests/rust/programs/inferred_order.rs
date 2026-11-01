#![no_std]
fn choose<T>(first:T,second:T)->T{second}
fn early()->u64{choose(return 0xfedcba9876543210,{while true{} 0u64})}
pub extern "C" fn answer()->u64{
    let mut n=0u64;
    let v=choose({n+=1;12u64},{n*=2;16u64});
    if n==2 && v==16 {early()}else{0}
}
