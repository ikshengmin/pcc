package main; type I interface{ M(Box[int]) }; type Box[T any] struct{ V T }; func main(){}
