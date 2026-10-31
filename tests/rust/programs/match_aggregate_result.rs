#![no_std]
struct Pair { a:u64, b:u64 }
fn build(x:bool) -> Pair {match x {false=>Pair{a:1,b:2},true=>Pair{a:0xfedcba9876543210,b:7}}}
pub extern "C" fn answer() -> u64 {let p=build(true); p.a}
