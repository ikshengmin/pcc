// Separate executable fixture, not the private-answer certification original.
mod provider {
    pub fn value() -> u64 { 42 }
}
use provider as u64;
extern crate self as original;
use original as usize;
fn through_module() -> usize { u64::value() as usize }
pub fn answer() -> u64 { usize::through_module() as u64 }
