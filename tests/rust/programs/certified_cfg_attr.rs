//! Authored cfg_attr policy fixture, not an extracted upstream success.
#![warn(missing_docs)]
#![cfg_attr(not(feature = "std"), no_std)]

pub extern "C" fn answer() -> u64 {
    0xfedcba9876543210
}
