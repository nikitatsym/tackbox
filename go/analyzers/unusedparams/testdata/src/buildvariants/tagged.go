//go:build tackbox_unusedparams_alternate

package buildvariants

func withTaggedTwin(value int) int { return value }
func namedWithTwin(value int) int { return value }
func (receiver) withMethodTwin(value int) int { return value }
