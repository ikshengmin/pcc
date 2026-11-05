struct R{x:u64}
fn identity(x:R)->R{x}
pub fn answer()->u64{
    let mut order=0u64;
    let _=identity({order=order*10+1;R{x:order}});
    let _:u64={order=order*10+2;order};
    let _=[{order=order*10+3;order},{order=order*10+4;order}];
    if order==1234u64{0xfedcba9876543210}else{0}
}
