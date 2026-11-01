struct R{rows:[[u64;2];2],flags:[bool;2]}
pub fn answer()->u64{let mut r=R{rows:[[1,2],[3,4]],flags:[false,true]};let old=r.rows;r.rows[1][0]=0xfedcba9876543210;if old[1][0]!=3||r.flags[0]||!r.flags[1]{return 0;}r.rows[1][0]}
