#![no_std]
/// A plain hint does not change arithmetic or evaluation order.
#[inline]
fn first(value: u64) -> u64 { value + 2 }
#[inline(always)]
fn identity<T>(value: T) -> T { value }
#[inline(never)]
fn last(value: u64) -> u64 { value * 2 }
pub fn answer() -> u64 {
    if last(identity(first(19))) == 42 { 0xfedcba9876543210u64 } else { 0 }
}
