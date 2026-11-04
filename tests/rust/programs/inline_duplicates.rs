#[inline(never)]
/// Documentation may appear between repeated hints.
#[inline(always)]
#[doc = "The first inline hint wins."]
#[inline]
fn value() -> u64 { 42 }
pub fn answer() -> u64 {
    if value() == 42 { 0xfedcba9876543210u64 } else { 0 }
}
