struct Depth{n:u64}
impl Depth{
    fn pass<U>(self,x:U)->U{
        if self.n==0{x}else{Depth{n:self.n-1}.pass(x)}
    }
}
fn relay<V>(x:V)->V{Depth{n:3}.pass(x)}
pub fn answer()->u64{
    if relay(42u64)==42 && relay(true){0xfedcba9876543210u64}else{0}
}
