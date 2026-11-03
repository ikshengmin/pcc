//! Authored conditional-item observer, not an upstream project substitute.
#![warn(missing_docs)]
#![cfg_attr(not(feature = "std"), no_std)]

#[cfg(feature = "std")]
extern crate core;

pub extern "C" fn answer() -> u64 {
    0xfedcba9876543210
}
