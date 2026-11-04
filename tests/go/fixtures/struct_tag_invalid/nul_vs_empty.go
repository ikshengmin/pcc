package main
type NUL = struct{ X uint64 "\x00" }
type Empty = struct{ X uint64 "" }
func main() {
	var nul NUL
	var empty Empty = nul
	_ = empty
}
