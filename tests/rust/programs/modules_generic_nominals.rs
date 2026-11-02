mod a {
    pub struct R{pub value:u64}
    pub struct Box<T>{pub value:T}
    pub fn make()->R{R{value:40}}
    pub fn id<T>(x:T)->T{x}
    pub fn empty<T>()->[T;0]{[]}
}
mod b {
    pub struct R{pub value:u64}
    pub fn make()->R{R{value:2}}
    pub fn id<T>(x:T)->T{crate::a::id(x)}
}
fn forward<T>(x:T)->T{b::id(x)}
pub fn answer()->u64 {
    let a:a::R=forward(a::make());
    let b:b::R=b::id::<b::R>(b::make());
    let box_value=a::Box::<u64>{value:a.value+b.value};
    let empty:[bool;0]=a::empty();
    if box_value.value==42 && empty.len()==0 {0xfedcba9876543210u64} else {0}
}
