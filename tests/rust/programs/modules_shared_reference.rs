mod data { pub struct R{pub value:u64} }
mod ops {
    pub fn read(x:&crate::data::R)->u64{x.value}
    pub fn twice(x:&crate::data::R)->u64{self::read(x)+self::read(x)}
}
pub fn answer()->u64 {
    let r=data::R{value:21};
    let alias=&r;
    if ops::twice(alias)==42 {0xfedcba9876543210u64} else {0}
}
