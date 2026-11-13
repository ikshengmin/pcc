// Original PCC specification fixture: identifier patterns with `@` subpatterns
// (Rust Reference patterns.html#identifier-patterns). The or-subpattern shape
// is the one uv 0.13.7's crates/uv/src/lib.rs matches on `ToolCommand`.
#![no_std]
enum Tool { Uvx(u64), Run(u64), Other }
fn digit(n: u64) -> u64 {
    match n { small @ 1 => small + 10, any @ _ => any }
}
fn variant(command: Tool) -> u64 {
    match command {
        run_variant @ (Tool::Uvx(_) | Tool::Run(_)) => match run_variant {
            Tool::Uvx(x) => x,
            Tool::Run(x) => x + 1,
            Tool::Other => 0,
        },
        Tool::Other => 99,
    }
}
fn payload(command: Tool) -> u64 {
    match command {
        whole @ Tool::Run(inner @ 7) => inner + match whole { Tool::Run(x) => x, _ => 0 },
        Tool::Run(other) => other,
        _ => 0,
    }
}
fn pair(p: (bool, u64)) -> u64 {
    match p {
        all @ (true, n) => match all { (_, m) => m + n },
        mut rest @ (false, _) => {
            match rest { (_, m) => rest = (true, m + 4) }
            match rest { (_, m) => m * 3 }
        }
    }
}
pub extern "C" fn answer() -> u64 {
    if digit(1) != 11 || digit(5) != 5 { return 0; }
    if variant(Tool::Uvx(4)) != 4 || variant(Tool::Run(4)) != 5 || variant(Tool::Other) != 99 { return 0; }
    if payload(Tool::Run(7)) != 14 || payload(Tool::Run(8)) != 8 { return 0; }
    if pair((true, 6)) != 12 || pair((false, 1)) != 15 { return 0; }
    0xfedcba9876543210
}
