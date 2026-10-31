// Original PCC regression fixture, repository MIT license.
package main

import (
	calc "example/arith"
	"example/adjust"
)

func Result() int64 {
	return calc.Combine(21, adjust.Offset) + calc.Base
}

func main() {
	if Result() != 2109 { for {} }
}
