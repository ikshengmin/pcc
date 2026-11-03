mod provider {
    pub fn value()->u64 { 0xfedcba9876543210u64 }
}
use crate::provider as storage;
pub fn answer()->u64 { storage::value() }
