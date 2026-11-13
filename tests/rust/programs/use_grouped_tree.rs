// Original PCC specification fixture: grouped use trees (Rust Reference
// items/use-declarations.html), nested groups, renames, a trailing comma and
// an empty-prefix group, as in `use std::collections::{BTreeMap, ...};`.
mod provider {
    pub fn base()->u64 { 0xfedcba9876543200u64 }
    pub mod nested {
        pub fn low()->u64 { 0x10 }
        pub fn offset()->u64 { 0 }
        pub struct Pair { pub x: u64 }
    }
    pub enum Kind { Small, Large }
}
use crate::provider::{base, nested::{low as small, Pair}, Kind,};
use {provider::nested::offset};
pub fn answer()->u64 {
    let pair = Pair { x: small() };
    let bump = match Kind::Large { Kind::Small => 0, Kind::Large => pair.x };
    base() + bump + offset()
}
