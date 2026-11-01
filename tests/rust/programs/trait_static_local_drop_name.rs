trait Drop { fn drop(&mut self); }
struct R { x:u64 }
impl Drop for R { fn drop(&mut self) {self.x=42;} }
pub fn answer()->u64 {
    let mut r=R{x:1};
    <R as Drop>::drop(&mut r);
    if r.x==42 {0xfedcba9876543210u64} else {0}
}
