package main

type AbsentA = struct{ X uint64 }
type EmptyA = struct{ X uint64 "" }
type EmptyB = struct{ X uint64 "" }
type RawEmpty = struct{ X uint64 `` }
type RawJSON = struct{ X uint64 `json:"x"` }
type QuotedJSON = struct{ X uint64 "json:\"x\"" }
type RawCR = struct{ X uint64 `ab` }
type ElidedCR = struct{ X uint64 "ab" }
type HexByte = struct{ X uint64 "\xff" }
type OctalByte = struct{ X uint64 "\377" }
type UnicodeEscape = struct{ X uint64 "\u00e9" }
type UTF8Bytes = struct{ X uint64 "\xc3\xa9" }
type UnicodeLiteral = struct{ X uint64 `é` }
type LongUnicodeEscape = struct{ X uint64 "\U000000e9" }
type HexNUL = struct{ X uint64 "\x00" }
type OctalNUL = struct{ X uint64 "\000" }

func Result() uint64 {
	var a AbsentA = AbsentA{X: 1}
	var b EmptyA = a
	var c EmptyB = EmptyB{X: 2}
	var d RawEmpty = c
	var e RawJSON = RawJSON{X: 4}
	var f QuotedJSON = e
	var g RawCR = RawCR{X: 8}
	var h ElidedCR = g
	var i HexByte = HexByte{X: 16}
	var j OctalByte = i
	var k UnicodeEscape = UnicodeEscape{X: 32}
	var l UTF8Bytes = k
	var m UnicodeLiteral = UnicodeLiteral{X: 64}
	var n LongUnicodeEscape = m
	var o HexNUL = HexNUL{X: 128}
	var p OctalNUL = o
	return b.X + d.X + f.X + h.X + j.X + l.X + n.X + p.X
}

func main() {
	if Result() != 255 { panic("tag spelling identity") }
}
