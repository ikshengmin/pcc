package main

// These canonical named references break anonymous type expansion. The
// declarations describe signatures only, not stored interface values.
type Direct interface { Next(Direct) Direct }
type Alias = Direct
type ThroughAlias interface { Next(Alias) Alias }
type Left interface { Next() Right }
type Right interface { Next() Left }
type WithAnonymous interface { Use(interface { Right }) }
type Wrapped interface { Use(*Direct, []Direct, [0]Direct) }
type Variadic interface { Use(...Direct) }
type Results interface { Next() (Direct, []Direct) }
type EmbeddedAlias = interface { Direct }
type ViaEmbeddedAlias interface { Use(EmbeddedAlias) }
type ForwardAlias = Forward
type Forward interface { Next() ForwardAlias }

func Result() uint64 { return 0xfedcba9876543210 }
func main() { if Result() != 0xfedcba9876543210 { for {} } }
