#![no_std]
fn twice<T: Copy>(value: T) -> (T, T) { (value, value) }
pub extern "C" fn answer() -> u64 {
    let numbers = twice(21u64);
    let arrays = twice([20u64, 22]);
    let tuples = twice((true, 42u64));
    let empty = twice(());
    let first_unit = empty.0;
    let second_unit = empty.1;
    if numbers.0 + numbers.1 == 42 && arrays.0[0] + arrays.1[1] == 42
        && tuples.0.0 && tuples.1.1 == 42 {
        0xfedcba9876543210
    } else { 0 }
}
