struct Counter { steps: u64 }
impl Counter {
    fn pair<U: Copy>(&self, value: U) -> (U, U) { (value, value) }
    fn tick<U: Copy>(&mut self, value: U) -> (U, U) {
        self.steps += 1;
        (value, value)
    }
}
pub fn answer() -> u64 {
    let mut c = Counter { steps: 0 };
    let mut trace = 0u64;
    let a = ({ trace = trace * 10 + 1; &c }).pair({ trace = trace * 10 + 2; 21u64 });
    let b = ({ trace = trace * 10 + 3; &mut c }).tick({ trace = trace * 10 + 4; 21u64 });
    let shared = &c;
    let yes = shared.pair(true);
    let mutable = &mut c;
    let d = mutable.tick(20u64);
    let e = mutable.tick::<u64>(22u64);
    if trace == 1234 && c.steps == 3 && a.0 + a.1 == 42 && b.0 + b.1 == 42
        && d.0 + e.1 == 42 && yes.0 && yes.1 { 0xfedcba9876543210 } else { 0 }
}
