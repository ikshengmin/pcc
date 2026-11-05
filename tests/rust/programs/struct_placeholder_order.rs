struct Pair<T,U>{first:T,second:U}
pub fn answer()->u64{
    let mut order=0u64;
    let pair=Pair::<_,_>{second:{order=order*10+2;order},first:{order=order*10+1;order}};
    if pair.second==2u64 && pair.first==21u64 && order==21u64{
        0xfedcba9876543210
    }else{0}
}
