// Original PCC specification fixture: owned cfg selection (Rust Reference
// conditional-compilation.html) over features, target cfg, test,
// debug_assertions and panic, on modules, functions and use items. Compile
// with --cfg 'feature="fast"' -C debug-assertions=no -C panic=abort.
#[cfg(feature = "fast")]
mod speed { pub fn bits()->u64 { 0x10 } }
#[cfg(not(feature = "fast"))]
mod speed { pub fn bits()->u64 { 0x20 } }
#[cfg(all(unix, target_pointer_width = "64", not(windows)))]
fn base()->u64 { 0xfedcba9876543200u64 }
#[cfg(any(windows, not(target_pointer_width = "64")))]
fn base()->u64 { 0 }
// Disabled: its file does not exist and is never loaded.
#[cfg(feature = "slow")]
mod absent;
#[cfg(test)]
fn extra()->u64 { 1 }
#[cfg(not(test))]
fn extra()->u64 { 0 }
#[cfg(debug_assertions)]
fn checks()->u64 { 2 }
#[cfg(not(debug_assertions))]
fn checks()->u64 { 0 }
#[cfg(panic = "unwind")]
use speed::bits as chosen;
#[cfg(panic = "abort")]
use crate::speed::bits as chosen;
pub fn answer()->u64 { base() + chosen() + extra() + checks() }
