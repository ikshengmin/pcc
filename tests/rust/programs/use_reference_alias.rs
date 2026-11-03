mod provider {
    pub fn value(item:&u64)->u64 { *item }
}
use crate::provider::value as read;
pub fn answer()->u64 { let item=0xfedcba9876543210u64; read(&item) }
