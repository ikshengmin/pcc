// Original PCC component observer; the accompanying generated table is unchanged.
package bits
func Read(x uint8) uint32 {
    return uint32(ntz8tab[x]) | uint32(pop8tab[x])<<8 | uint32(rev8tab[x])<<16 | uint32(len8tab[x])<<24
}
