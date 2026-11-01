struct R{n:u64}
impl R{fn choose<U>(self,first:U,last:U)->U{first}}
fn make(p:&mut u64)->R{*p=*p*10+1;R{n:1}}
fn argument(p:&mut u64)->u64{*p=*p*10+2;*p}
fn early(p:&mut u64)->u64{R{n:0}.choose::<u64>({return 42;},argument(p))}
pub fn answer()->u64{
    let mut n=0u64;
    let a=make(&mut n).choose(argument(&mut n),argument(&mut n));
    let b=early(&mut n);
    if n==122 && a==12 && b==42{0xfedcba9876543210u64}else{0}
}
