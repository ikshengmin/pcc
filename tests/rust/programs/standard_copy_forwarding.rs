fn twice<T: Copy>(value: T) -> (T, T) { (value, value) }
fn forward<U: Copy>(value: U) -> (U, U) { twice(value) }
fn require_sized<T: Sized>() {}
fn relaxed_copy<T: ?Sized + Copy>(value: T) -> T { require_sized::<T>(); value }
fn empty<T: Copy>() -> [T; 0] { [] }
pub extern "C" fn answer() -> u64 {
    let values = forward(21u64);
    let inferred: [bool; 0] = empty();
    if relaxed_copy(values.0) + values.1 == 42 && inferred.len() == 0usize {
        0xfedcba9876543210
    } else { 0 }
}
