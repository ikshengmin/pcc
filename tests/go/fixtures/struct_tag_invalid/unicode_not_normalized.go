package main
type Composed = struct{ X uint64 `é` }
type Decomposed = struct{ X uint64 `é` }
func main() {
	var composed Composed
	var decomposed Decomposed = composed
	_ = decomposed
}
