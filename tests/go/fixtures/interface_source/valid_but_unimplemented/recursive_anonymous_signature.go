package main

type Recursive interface {
	Visit(interface { Recursive })
}

func main() {}
