trait Pass { fn pass<R>(self,x:R)->R; fn id<U>(x:U)->U; }
struct R { x:u64 }
impl Pass for R {
    fn pass<R>(self,x:R)->R { <Self as Pass>::id(x) }
    fn id<V>(x:V)->V { x }
}
trait Again { fn again<R>(self,x:R)->Self; }
struct W<T>{x:T}
impl Again for W<R> {
    fn again<R>(self,x:R)->Self { <Self as PassThrough>::keep::<R>(self,x) }
}
trait PassThrough { fn keep<U>(self,x:U)->Self; }
impl PassThrough for W<R> { fn keep<U>(self,x:U)->Self {self} }
pub fn answer()->u64 {
    let a=R{x:1}.pass(42u64);
    let b=W::<R>{x:R{x:42}}.again(true).x.x;
    if a==b {0xfedcba9876543210u64} else {0}
}
