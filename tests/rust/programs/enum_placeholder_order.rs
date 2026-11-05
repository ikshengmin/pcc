enum Pair<T,U>{Value(T,U)}
fn read(value:Pair<u64,u64>)->bool{match value{Pair::Value(a,b)=>a==1 && b==12}}
pub fn answer()->u64{
    let mut order=0u64;
    let pair=Pair::<_,_>::Value({order=order*10+1;order},{order=order*10+2;order});
    if read(pair) && order==12u64{0xfedcba9876543210}else{0}
}
