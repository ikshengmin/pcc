struct Pair<T,U>{a:T,b:U}
pub fn answer()->u64{
    let mut trace=0;
    let pair=Pair{
        b:{trace=trace*10+2;true},
        a:{trace=trace*10+1;0xfedcba9876543210},
    };
    if trace==21 && pair.b{pair.a}else{0}
}
