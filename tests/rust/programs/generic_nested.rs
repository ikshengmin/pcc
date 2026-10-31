#![no_std]
struct Wrap<T> { value: T }
enum Maybe<T> { None, Some(T) }
fn answer() -> u64 {
    let nested:Wrap<Wrap<u64>>=Wrap::<Wrap<u64>> {
        value: Wrap::<u64> { value: 0xfedcba9876543210u64 },
    };
    let choice = Maybe::<Wrap<Wrap<u64>>>::Some(nested);
    match choice {
        Maybe::<Wrap<Wrap<u64>>>::None => 0,
        Maybe::<Wrap<Wrap<u64>>>::Some(value) => value.value.value,
    }
}
