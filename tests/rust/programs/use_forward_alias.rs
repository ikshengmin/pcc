use self::second as first;
use crate::provider::value as second;
mod provider {
    pub fn value()->u64 { 0xfedcba9876543210u64 }
}
pub fn answer()->u64 { first() }
