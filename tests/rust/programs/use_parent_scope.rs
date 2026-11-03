mod provider {
    fn value()->u64 { 0xfedcba9876543210u64 }
    pub mod reader {
        use super::value as read;
        pub fn answer()->u64 { read() }
    }
}
pub fn answer()->u64 { provider::reader::answer() }
