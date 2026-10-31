// Original PCC regression fixture, repository MIT license.
package arithmetic

import "example/adjust"

const Base = adjust.Offset
func Combine(a, b int) int64 { return int64(a*100+b+adjust.Extra) }
func hidden() int { return 0 }
