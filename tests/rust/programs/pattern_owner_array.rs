enum E<T>{Some(T)}
pub fn answer()->u64{
    let n=match E::<_>::Some((E::<_>::Some([0xfedcba9876543210,2]),true)){
        E::<(E<[_;2]>,_)>::Some((E::<_>::Some(values),true))=>values[0],
        E::<(_,bool)>::Some((_,false))=>0,
    };
    n
}
