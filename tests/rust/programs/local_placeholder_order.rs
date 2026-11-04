fn pair<T,U>(first:T,second:U)->(T,U){(first,second)}
pub fn answer()->u64{
    let mut order=0u64;
    let values:(_,_)=pair({order=order*10+1;order},{order=order*10+2;order});
    let shadow:_=false;
    {let shadow:_=values.0;order=order*10+shadow;}
    if values.0==1u64 && values.1==12u64 && order==121u64 && !shadow{
        0xfedcba9876543210
    }else{0}
}
