extern crate self as original;
mod provider {
    pub fn value()->u64 { 0xfedcba9876543210u64 }
}
use original::provider::value as read;
pub fn answer()->u64 { read() }
