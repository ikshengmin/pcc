struct Payload { value: u64 }
struct Owner<T> { payload: T }
impl<T> Owner<T> {
    fn pair<U: Copy>(self, value: U) -> (T, U, U) { (self.payload, value, value) }
}
pub fn answer() -> u64 {
    let a = Owner::<Payload> { payload: Payload { value: 40 } }.pair(1u64);
    let b = Owner::<Payload>::pair::<bool>(Owner::<Payload> { payload: Payload { value: 42 } }, true);
    if a.0.value + a.1 + a.2 == 42 && b.0.value == 42 && b.1 && b.2 {
        0xfedcba9876543210
    } else { 0 }
}
