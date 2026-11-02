mod inner {
    pub fn value() -> u64 { 0xfedcba9876543210u64 }
}
pub fn answer() -> u64 { inner::value() }
