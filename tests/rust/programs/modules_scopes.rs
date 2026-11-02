fn seed()->u64{40}
mod left {
    fn seed()->u64{1}
    pub mod child { pub fn value()->u64{super::seed()} }
    pub fn value()->u64{crate::seed()+self::child::value()}
}
mod right { pub fn value()->u64{1} }
pub fn answer()->u64 {
    if left::value()+right::value()==42 {0xfedcba9876543210u64} else {0}
}
