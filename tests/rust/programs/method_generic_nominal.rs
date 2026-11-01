struct Left<T>{v:T}
struct Right<T>{v:T}
struct Item{n:u64}
impl<T> Left<T>{fn echo<U>(self,x:U)->U{x}}
impl<T> Right<T>{fn echo<U>(self,x:U)->U{x}}
pub fn answer()->u64{
    let a=Left::<u64>{v:1}.echo(Item{n:17});
    let b=Right::<u64>{v:2}.echo::<Item>(Item{n:25});
    let c=Left::<u32>{v:3}.echo(true);
    if a.n+b.n==42 && c{0xfedcba9876543210u64}else{0}
}
