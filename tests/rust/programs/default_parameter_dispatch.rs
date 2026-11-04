trait Read { fn read(&self) -> u64; }
trait Apply {
    fn apply<T: Read>(&self, value: &T) -> u64 { value.read() + 2 }
}
struct R { value: u64 }
struct Runner { value: u64 }
impl Read for R { fn read(&self) -> u64 { self.value } }
impl R { fn read(&self) -> u64 { 400 } }
impl Apply for Runner {}
pub fn answer() -> u64 {
    let runner = Runner { value: 0 };
    let value = R { value: 40 };
    if runner.apply(&value) == 42 && value.read() == 400 {
        0xfedcba9876543210u64
    } else { 0 }
}
