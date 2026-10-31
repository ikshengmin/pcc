#![no_std]
enum Maybe<T> { None, Some(T) }
enum Outcome<T, E> { Good(T), Bad(E) }
fn read(value: Maybe<u64>) -> u64 {
    match value { Maybe::None => 0, Maybe::Some(number) => number }
}
fn outcome(value: Outcome<i64, u64>) -> u64 {
    match value { Outcome::Good(number) => number as u64, Outcome::Bad(code) => code }
}
fn answer() -> u64 {
    if read(Maybe::<u64>::None) != 0 { return 0; }
    if outcome(Outcome::<i64, u64>::Good(-7)) != 18446744073709551609u64 { return 0; }
    let result = outcome(Outcome::<i64, u64>::Bad(0xfedcba9876543210u64));
    read(Maybe::<u64>::Some(result))
}
