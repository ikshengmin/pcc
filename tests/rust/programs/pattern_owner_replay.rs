enum E<T>{Some(T),None}
fn empty<T>()->E<T>{E::<_>::None}
fn choose<U>(fallback:U)->U{
    match empty(){E::<_>::None=>fallback,E::<_>::Some(x)=>x}
}
pub fn answer()->u64{
    if choose(true) && choose(9u32)==9u32{
        choose(0xfedcba9876543210)
    }else{0}
}
