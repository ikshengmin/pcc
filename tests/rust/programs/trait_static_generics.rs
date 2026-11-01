trait Pick { fn pick<U>(self,x:U)->U; fn empty<U>()->[U;0]; }
struct R { x:u64 }
impl Pick for R {
    fn pick<V>(self,x:V)->V { x }
    fn empty<V>()->[V;0] { [] }
}
fn outer<T>(x:T)->T { R{x:1}.pick(x) }
pub fn answer()->u64 {
    let x:u64=R{x:1}.pick(40);
    let y=<R as Pick>::pick::<u64>(R{x:2},2);
    let z:[bool;0]=R::empty();
    if outer(x+y)==42 && z.len()==0 {0xfedcba9876543210u64} else {0}
}
