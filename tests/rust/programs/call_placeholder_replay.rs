mod helper{
    pub struct Value{pub n:u64}
    pub fn id<T>(x:T)->T{x}
}
fn forward<Value>(x:Value,n:u64)->Value{
    if n==0{helper::id::<_>(x)}else{forward::<_>(x,n-1)}
}
pub fn answer()->u64{
    let flag=forward::<_>(true,2);
    let record=forward::<_>(helper::Value{n:0xfedcba9876543210},3);
    let explicit=helper::id::<helper::Value>(record);
    if flag{explicit.n}else{0}
}
