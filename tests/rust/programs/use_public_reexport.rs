// Original PCC specification fixture: `pub use` re-exports (Rust Reference
// items/use-declarations.html, "use Visibility"), as uv-lock re-exports
// `crate::lock::deserialize::Error as CanonicalLockError`.
mod private_impl {
    pub fn base()->u64 { 0xfedcba9876543200u64 }
    pub mod deep {
        pub fn low()->u64 { 0x0c }
    }
    pub struct Pair { pub x: u64 }
}
pub use private_impl::base;
mod facade {
    pub use crate::private_impl::{deep::low as small, Pair};
}
mod outer {
    // A re-export of a re-export, reached from outside both modules.
    pub use super::facade::small as tiny;
    pub use super::facade::Pair as Couple;
}
pub fn answer()->u64 {
    let pair = outer::Couple { x: facade::small() };
    let other = facade::Pair { x: outer::tiny() / 3 };
    base() + pair.x + other.x
}
