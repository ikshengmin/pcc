trait Copy {}
trait Sized {}
struct R { value: u64 }
impl Copy for R {}
impl Sized for R {}
fn twice<T: core::marker::Copy>(value: T) -> (T, T) { (value, value) }
fn forward<Copy, T: std::marker::Copy + core::marker::Sized>(ignored: Copy, value: T) -> (T, T) { twice(value) }
pub extern "C" fn answer() -> u64 {
    let values = forward(R { value: 9 }, 21u64);
    if values.0 + values.1 == 42 { 0xfedcba9876543210 } else { 0 }
}
