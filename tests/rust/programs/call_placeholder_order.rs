struct Receiver{value:u64}
impl Receiver{fn first<T,U>(self,first:T,last:U)->T{first}}
fn make(trace:&mut u64)->Receiver{*trace=*trace*10+1;Receiver{value:0}}
fn operand(trace:&mut u64)->u64{*trace=*trace*10+2;*trace}
fn early(trace:&mut u64)->u64{
    Receiver{value:0}.first::<u64,_>({return 42;},operand(trace))
}
pub fn answer()->u64{
    let mut trace=0u64;
    let first=make(&mut trace).first::<_,u64>(operand(&mut trace),operand(&mut trace));
    let returned=early(&mut trace);
    if trace==122u64 && first==12u64 && returned==42u64{0xfedcba9876543210u64}else{0}
}
