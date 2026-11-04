struct R { value: u64 }
enum Choice<T> { Present(T), Missing }
fn identity<T>(value: T) -> T {
    if let result = value { result } else { loop {} }
}
fn choose<T>(value: Choice<T>, fallback: T) -> T {
    if let Choice::Present(item) = value { item } else { fallback }
}
pub fn answer() -> u64 {
    let first = identity(R { value: 40 });
    let second = choose(Choice::<u64>::Missing, 2u64);
    let selected = choose(Choice::<bool>::Present(true), false);
    if first.value + second == 42 && selected && identity(true) {
        0xfedcba9876543210u64
    } else { 0 }
}
