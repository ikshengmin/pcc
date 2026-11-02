fn pair<A,B>(a:A,b:B)->(A,B){(a,b)}
fn id<T>(x:T)->T{x}
pub fn answer()->u64{
    let values=pair::<_,u64>(true,0xfedcba9876543210);
    let wide=id::<_>(4294967296);
    let later:u64=wide;
    let flag=id::<_>(true);
    let small=id::<_>(3u32);
    if values.0 && flag && later==4294967296u64 && small==3u32{values.1}else{0}
}
