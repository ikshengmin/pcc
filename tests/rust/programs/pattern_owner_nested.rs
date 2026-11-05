enum E<T>{Some(T)}
pub fn answer()->u64{
    let n=match E::<_>::Some((E::<_>::Some((0xfedcba9876543210,2u32)),true)){
        E::<(E<(_,u32)>,_)>::Some((E::<_>::Some((value,tag)),true))=>if tag==2u32{value}else{0},
        E::<(_,bool)>::Some((_,false))=>0,
    };
    n
}
