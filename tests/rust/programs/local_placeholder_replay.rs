fn id<T>(value:T)->T{value}
fn outer<U>(value:U)->U{let local:_=id::<_>(value);local}
pub fn answer()->u64{
    let first:_=outer(true);
    let second:_=outer(0xfedcba9876543210);
    let third:_=outer(7u32);
    if first && third==7u32{second}else{0}
}
