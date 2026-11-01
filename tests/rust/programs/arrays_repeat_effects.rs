fn tick(p:&mut u64)->u64{*p+=1;*p}
pub fn answer()->u64{let mut c=0;let z:[u64;0]=[tick(&mut c);0];if c!=1||z.len()!=0usize{return 0;}let a=[tick(&mut c);3];if c!=2||a[0]!=2||a[1]!=2||a[2]!=2{return 0;}let b=[tick(&mut c),tick(&mut c),tick(&mut c)];if c!=5||b[0]!=3||b[1]!=4||b[2]!=5{return 0;}0xfedcba9876543210}
