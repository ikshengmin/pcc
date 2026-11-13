// Original PCC specification fixture: built-in derives (Rust Reference
// attributes/derive.html), as uv's `GlobalInitialization` derives
// Debug, Copy, Clone, Eq and PartialEq. Copy values stay usable after a
// copy, `.clone()` and `==` borrow rather than move, and derived equality
// compares fields, tuples, arrays, variants and only active payloads.
#[derive(Debug, Copy, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
enum Initialization { Initialize, Reuse }
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
struct Point { x: u64, y: bool, tag: (u32, bool), grid: [u64; 2] }
#[derive(Clone, PartialEq, Debug)]
enum Shape { Dot(Point), Line(Point, Point), Empty }
#[derive(Clone, PartialEq)]
struct Owner { shape: Shape, id: u64 }
fn consume(owner: Owner) -> u64 { owner.id }
fn check() -> bool {
    let start = Initialization::Initialize;
    let again = start;
    let p = Point { x: 1, y: true, tag: (7, false), grid: [3, 4] };
    let q = p;
    let mut r = p;
    r.grid = [3, 5];
    let line = Shape::Line(p, q);
    let owner = Owner { shape: line.clone(), id: 9 };
    let copy = owner.clone();
    let same = owner == copy && !(owner != copy);
    let total = consume(owner) + consume(copy);
    start == again && start != Initialization::Reuse && p == q && p != r
        && line == Shape::Line(q, p) && line != Shape::Dot(p) && Shape::Empty == Shape::Empty
        && Shape::Dot(p) != Shape::Empty && same && total == 18
}
pub fn answer() -> &'static str { if check() { "derives agree" } else { "derives differ" } }
