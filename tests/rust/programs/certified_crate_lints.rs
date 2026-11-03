//! Authored lint-bearing native fixture, not an upstream crate extraction.
#![no_std]
#![warn(missing_docs)]

pub extern "C" fn answer() -> u64 {
    0xfedcba9876543210
}
