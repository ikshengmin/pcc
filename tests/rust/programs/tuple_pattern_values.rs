enum E<T>{None,Some(T)}
pub fn answer()->u64{
    let mut value=E::<_>::None;
    let before=match value{E::None=>0,E::Some((x,flag))=>if flag{x}else{0}};
    value=E::<_>::Some((0xfedcba9876543210,true));
    if before==0{match value{E::None=>0,E::Some((x,true))=>x,E::Some((_,false))=>0}}else{0}
}
