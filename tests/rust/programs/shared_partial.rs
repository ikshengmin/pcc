#![no_std]
struct Leaf{n:u64}
struct Pair{left:Leaf,right:Leaf}
fn read(p:&Leaf,n:u64)->u64{p.n+n}
pub extern "C" fn answer()->u64{
    let mut p=Pair{left:Leaf{n:1},right:Leaf{n:0xfedcba9876543200}};
    let left=p.left;
    let first=read(&p.right,left.n);
    p.left=Leaf{n:0};
    read(&p.right,{p.left.n=16;p.left.n})+first-first
}
