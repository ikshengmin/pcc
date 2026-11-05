enum E<T>{None,Some(T)}
fn empty<U>()->E<U>{E::<_>::None}
fn choose<U>(fallback:U)->U{
    match empty(){E::None=>fallback,E::Some((x,true))=>x,E::Some((x,false))=>x}
}
pub fn answer()->u64{
    if choose(true) && choose(9u32)==9u32{choose(0xfedcba9876543210)}else{0}
}
