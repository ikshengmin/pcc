trait Step { fn step(&mut self, delta: u64) -> u64; }
struct R { x: u64 }
impl Step for R { fn step(&mut self, delta: u64) -> u64 { self.x += delta; self.x } }
impl R { fn step(&mut self, delta: u64) -> u64 { 900 } }
fn apply<T>(value: &mut T, delta: u64) -> u64 where T: Step { value.step(delta) }
fn forward<U>(value: &mut U) -> u64 where U: Step, U: Step { apply(value, 1) }
pub fn answer() -> u64 {
    let mut r = R { x: 39 };
    let mut trace = 0u64;
    let first = apply({ trace = trace * 10 + 1; &mut r }, { trace = trace * 10 + 2; 2u64 });
    let second = forward(&mut r);
    if first == 41 && second == 42 && r.x == 42 && trace == 12 && r.step(1) == 900 {
        0xfedcba9876543210
    } else { 0 }
}
