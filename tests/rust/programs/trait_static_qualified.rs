trait A { fn same(self)->u64; }
trait B { fn same(self)->u64; }
struct R { x:u64 }
impl A for R { fn same(self)->u64 { self.x+1 } }
impl B for R { fn same(self)->u64 { self.x+2 } }
impl R { fn same(self)->u64 { self.x+3 } }
pub fn answer()->u64 {
    let a=<R as A>::same(R{x:10});
    let b=<R as B>::same(R{x:10});
    let c=R{x:10}.same();
    let d=R::same(R{x:10});
    if a==11 && b==12 && c==13 && d==13 {0xfedcba9876543210u64} else {0}
}
