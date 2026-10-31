// Original PCC regression: a diverging constructor operand stops later effects.
#![no_std]
enum E { Missing, Pair(i64,u64) }
fn must_not_run()->u64 {while true {} 0}
fn early()->E {
    E::Pair(return E::Pair(-7,0xfedcba9876543210), must_not_run())
}
fn branch(flag:bool)->E {
    E::Pair(match flag {true=>return E::Missing,false=>-7},0xfedcba9876543210)
}
fn inspect(e:E)->u64 {match e{E::Missing=>0,E::Pair(tag,value)=>{if tag!=-7{return 0;}value}}}
pub extern "C" fn answer()->u64 {
    if inspect(branch(true))!=0{return 0;}
    if inspect(branch(false))!=0xfedcba9876543210{return 0;}
    inspect(early())
}
