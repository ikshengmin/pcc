package main

// Elided element literal types, the shape of math/bits' bits_test.go tables
// ({x, r} rows in an array of anonymous structs). Original PCC fixture.

type pair struct{ x, r uint64 }

type row struct {
	a, b uint32
	p    pair
}

type wrap struct{ v [2]pair }

func Rows() uint64 {
	var s uint64
	for i, p := range [...]pair{{1, 2}, {3, 4}, {x: 5}, {r: 6}} {
		s += uint64(i+1) * (p.x*10 + p.r)
	}
	return s
}

func Anonymous() uint64 {
	var s uint64
	for _, test := range [3]struct {
		x, r uint64
	}{
		{0x1, 0x8 << 60},
		{0x2, 0x4 << 60},
		{0x3, 0xc << 60},
	} {
		s = s*31 + (test.x<<8 | test.r>>60)
	}
	return s
}

func Indexed() uint64 {
	a := [5]pair{3: {7, 8}, 1: {r: 9}, {x: 4}}
	return a[1].r*1000 + a[2].x*100 + a[3].x*10 + a[3].r + a[0].x + a[4].r
}

func Nested() uint64 {
	// A named struct element whose own fields are explicit literals; the
	// element type elides, field values do not.
	t := [2]row{{1, 2, pair{3, 4}}, {a: 5, p: pair{r: 6}}}
	w := wrap{v: [2]pair{{8, 9}, {10, 11}}}
	return uint64(t[0].a)*100000 + uint64(t[0].b)*10000 + t[0].p.x*1000 + t[0].p.r*100 +
		uint64(t[1].a)*10 + t[1].p.r + w.v[1].x*w.v[0].r
}
