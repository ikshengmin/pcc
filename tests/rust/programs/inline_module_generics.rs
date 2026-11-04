mod values {
    #[inline(always)]
    pub fn identity<T>(value: T) -> T { value }
    mod inner {
        #[inline(never)]
        pub fn number() -> u64 { 42 }
    }
    #[inline]
    pub fn number() -> u64 { inner::number() }
}
pub fn answer() -> u64 {
    let number = values::identity(values::number());
    let truth = values::identity(true);
    if number == 42 && truth { 0xfedcba9876543210u64 } else { 0 }
}
