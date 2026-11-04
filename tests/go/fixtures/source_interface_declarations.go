package main

type Item int
type ItemAlias = Item
type First interface {
	Next() Second
	Apply(ItemAlias) Item
}
type Second interface {
	Previous() First
}
type Combined interface {
	First
	Apply(Item) Item
}
type Variadic interface {
	Collect(...int) (Item, error)
}
type Legacy interface {
	Error() string
}

func Keep[T any](value T) T {
	return value
}
func Same[T comparable](a, b T) bool {
	return a == b
}
func EmptyError() Legacy {
	return nil
}
func Result() int {
	if EmptyError() != nil || !Same(7, 7) {
		return -1
	}
	return Keep(40) + 2
}
func main() {
	if Result() != 42 {
		panic(1)
	}
}
