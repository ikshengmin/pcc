package main
type Raw = struct{ X uint64 `ab` }
type Interpreted = struct{ X uint64 "a\rb" }
func main() {
	var raw Raw
	var interpreted Interpreted = raw
	_ = interpreted
}
