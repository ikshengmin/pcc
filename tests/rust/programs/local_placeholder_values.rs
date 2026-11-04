fn id<T>(value:T)->T{value}
fn wide(value:u64)->u64{value}
pub fn answer()->u64{
    let value:_=id(0xfedcba9876543210);
    let other:_=true;
    let small:_=3u32;
    let defaulted:_=1;
    let widened=wide(value);
    if other && small==3u32 && defaulted==1i32{widened}else{0}
}
