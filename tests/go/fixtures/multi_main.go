// Original PCC regression fixture, repository MIT license.
package main

const answer = offset + 3
func Result() int64 { return int64(combine(4, answer)) }
func main() {
	if Result() != 409 { for {} }
}
