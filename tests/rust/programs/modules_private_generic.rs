mod a {
    fn same()->u64{40}
    pub struct Box<T>{pub value:T}
    pub fn id<T>(x:T)->T{let y:T=x;y}
    pub fn forward<T>(x:T)->T{self::id(x)}
    pub fn unbox<T>(x:Box<T>)->T{x.value}
    pub fn stamp<T>(x:T)->u64{same()}
}
mod b {
    struct R{x:u64}
    fn same()->u64{900}
    pub fn value()->u64 {
        let r=crate::a::forward(R{x:2});
        let boxed=crate::a::Box::<R>{value:r};
        let r=crate::a::unbox(boxed);
        let value=r.x;
        value+crate::a::stamp(R{x:same()})
    }
}
pub fn answer()->u64 {
    if b::value()==42 {0xfedcba9876543210u64} else {0}
}
