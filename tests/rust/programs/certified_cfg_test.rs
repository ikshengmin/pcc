//! Authenticated non-test library configuration.
#![warn(missing_docs)]
#![cfg_attr(not(test), cfg_attr(not(feature = "std"), no_std))]

#[cfg(test)]
mod compile_checks {
    use super::*;

    #[test]
    fn omitted() {
        assert_eq!(answer(), 0xfedcba9876543210);
    }
}

#[cfg(not(test))]
pub fn answer() -> u64 {
    0xfedcba9876543210
}
