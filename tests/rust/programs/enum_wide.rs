#![no_std]
#[repr(u64)] enum Wide { A=0x8000000000000000, B=0xfedcba9876543210 }
#[repr(u32)] enum Word { A=0xf0000000, B }
fn widen(x:Word) -> u64 {x as u64}
pub extern "C" fn answer() -> u64 {
    if widen(Word::B) != 0xf0000001 {return 0;}
    let x=Wide::B;
    match x {Wide::A=>0, Wide::B=>x as u64}
}
