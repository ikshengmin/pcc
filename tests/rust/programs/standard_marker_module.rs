mod other {
    pub struct Copy { pub value: u64 }
    pub fn twice<T: std::marker::Copy>(value: T) -> (T, T) { (value, value) }
}
mod consumer {
    pub fn calculate() -> u64 {
        let r = crate::other::Copy { value: 21 };
        let values = crate::other::twice(r.value);
        values.0 + values.1
    }
}
pub fn answer() -> u64 {
    if consumer::calculate() == 42 { 0xfedcba9876543210 } else { 0 }
}
