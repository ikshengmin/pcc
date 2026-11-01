trait Extract { fn extract(self)->u64; }
struct Boxed<T> { x:T }
impl Extract for Boxed<u64> { fn extract(self)->u64 { self.x } }
impl Extract for Boxed<bool> { fn extract(self)->u64 { if self.x {2} else {0} } }
pub fn answer()->u64 {
    let a=Boxed::<u64>{x:40}.extract();
    let b=<Boxed<bool> as Extract>::extract(Boxed::<bool>{x:true});
    if a+b==42 {0xfedcba9876543210u64} else {0}
}
