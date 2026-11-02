fn twice<T>(value: T) -> (T, T) where T: Copy, T: core::marker::Copy, { (value, value) }
fn forward<U: Copy>(value: U) -> (U, U) where U: Sized { twice(value) }
fn empty<T>() -> [T; 0] where T: Copy { [] }
fn sum<T>(left: T, right: T) -> (T, T) where T: Copy + { (left, right) }
pub fn answer() -> u64 {
    let pair = forward(21u64);
    let mut later = empty(); later = [0u32; 0];
    let flags: [bool; 0] = empty();
    let mut trace = 0u64;
    let ordered = sum({ trace = trace * 10 + 1; 20u64 }, { trace = trace * 10 + 2; 22u64 });
    if pair.0 + pair.1 == 42 && ordered.0 + ordered.1 == 42 && trace == 12
        && later.len() + flags.len() == 0usize { 0xfedcba9876543210 } else { 0 }
}
