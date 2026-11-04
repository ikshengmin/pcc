package main
type Byte = struct{ X uint64 "\xff" }
type Unicode = struct{ X uint64 "\u00ff" }
func main() {
	var bytes Byte
	var unicode Unicode = bytes
	_ = unicode
}
