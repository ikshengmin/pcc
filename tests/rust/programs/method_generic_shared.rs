struct Cell<T>{value:T}
impl<T> Cell<T>{
    fn pick<U>(&self,x:U)->U{x}
    fn choose<U>(&self,p:&u64,x:U)->U{if *p==0{return x;}x}
}
fn nested<V>(cell:&Cell<u64>,x:V)->V{cell.pick(x)}
pub fn answer()->u64{
    let c=Cell::<u64>{value:3};
    let zero=0u64;
    let a=c.pick(17u64);
    let b=Cell::<u64>::pick::<u64>(&c,25);
    let yes=nested(&c,true);
    let alias=&c;
    let alias_value=alias.pick(42u64);
    if a+b==42 && alias_value==42 && yes && c.choose(&zero,42u64)==42 && c.value==3{
        0xfedcba9876543210u64
    }else{0}
}
