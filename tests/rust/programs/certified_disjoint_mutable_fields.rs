struct R{left:u64,right:u64}
fn update(a:&mut u64,b:&mut u64){*a+=1;*b+=2;}
pub fn answer()->u64{let mut r=R{left:0xfedcba987654320d,right:0};update(&mut r.left,&mut r.right);r.left+r.right}
