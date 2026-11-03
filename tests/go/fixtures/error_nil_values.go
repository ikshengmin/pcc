// Complete authored nil-error-value program, repository MIT license.
package main

type Alias = error
type Named error

var effects int64

func identity(e error) error { return e }
func zero() (e error) { return }
func pair() (error, error) { return nil, nil }
func effect(n int64) error {
	effects = effects*10+n
	var e error
	return e
}

func Result() int64 {
	effects = 0
	var e error
	var n Named = e
	var a Alias = n
	e = identity(a)
	n = Named(error(nil))
	if e != nil || nil != n || zero() != nil { return -1 }
	x, y := pair()
	if x != nil || y != nil || x != y { return -2 }
	if effect(1) == nil && effect(2) != nil { return -3 }
	if effect(3) != effect(4) { return -4 }
	if effect(5) == effect(6) && effects != 123456 { return -5 }
	if effects != 123456 { return -6 }
	return 42
}

func main() {
	if Result() != 42 { panic(1) }
}
