enum E<T>{Some(T),None}
pub fn answer()->u64{
    match E::<_>::Some(E::<_>::Some((0xfedcba9876543210,true))){
        E::Some(E::Some((x,true)))=>x,
        E::Some(E::Some((_,false)))=>0,
        E::Some(E::None)=>0,
        E::None=>0,
    }
}
