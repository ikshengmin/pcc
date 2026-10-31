// Original PCC specification fixture. The upstream macro test remains whole.
#![no_std]
enum Flag { A, B }
fn quadrant(x: bool, y: bool) -> i64 {
    match (x,y) { (false,_) => 0, (_,false) => 1, (true,true) => 2 }
}
fn nested(x: (Flag,(bool,()))) -> u64 {
    match x {
        (Flag::A, (false | true, ())) => 17,
        (Flag::B, (false, _)) => 31,
        (Flag::B, (true, ())) => 0xfedcba9876543210,
    }
}
pub extern "C" fn answer() -> u64 {
    if quadrant(false,true)!=0 || quadrant(false,false)!=0 || quadrant(true,false)!=1 || quadrant(true,true)!=2 {return 0;}
    nested((Flag::B,(true,())))
}
