// Original PCC regression fixture, repository MIT license.
package main

func verify(ok bool) { if !ok { for {} } }
func trap() bool { for {} }
func yes() bool { return true }
func no() bool { return false }

func Result() int64 {
	x, y := 3, 7
	x, y = y, x
	verify(x == 7 && y == 3)
	x, z := 9, 11
	verify(x == 9 && z == 11)
	{
		x := 100
		verify(x == 100)
	}
	verify(x == 9)
	if q := 12; q < 10 {
		return -1
	} else if q == 12 {
		x += q
	} else { return -2 }
	var sum int
	for i := 0; i < 10; i++ {
		if i == 2 { continue }
		if i == 8 { break }
		sum += i
	}
	verify(sum == 26)
	for {
		sum++
		if sum == 30 { break }
	}
	verify(yes() || trap())
	verify(!(no() && trap()))
	verify((yes() && yes()) || trap())
	verify(!((no() || no()) && trap()))
	_ = sum
	return int64(x*1000 + y*100 + z*10 + sum)
}

func main() { verify(Result() == 21440) }
