package skiptest

import "testing"

func TestNoReason(t *testing.T) {
	t.Skip() // want `ERC008:.*Skip`
}

func TestEmptyReason(t *testing.T) {
	t.Skip("") // want `ERC008:.*Skip`
}

func TestWhitespaceReason(t *testing.T) {
	t.Skip("   ") // want `ERC008:.*Skip`
}

func TestWithReason(t *testing.T) {
	t.Skip("flaky upstream, tracked in issue 42")
}

func TestSkipfWithReason(t *testing.T) {
	t.Skipf("unsupported on %s", "plan9")
}

func TestSkipfEmptyFormat(t *testing.T) {
	t.Skipf("") // want `ERC008:.*Skipf`
}

func TestSkipNowBare(t *testing.T) {
	t.SkipNow() // want `ERC008:.*SkipNow`
}

func TestSkipNowMarked(t *testing.T) {
	// test-skip: covered by the integration suite instead
	t.SkipNow()
}

func TestSkipNowEmptyMarker(t *testing.T) {
	// test-skip:
	t.SkipNow() // want `ERC008:.*SkipNow`
}

func TestSkipNowMarkerNotAdjacent(t *testing.T) {
	// test-skip: too far away to count

	t.SkipNow() // want `ERC008:.*SkipNow`
}

func TestDynamicReason(t *testing.T) {
	reason := "computed at runtime"
	t.Skip(reason)
}

func TestErrValueReason(t *testing.T) {
	err := doWork()
	t.Skip(err)
}

func BenchmarkNoReason(b *testing.B) {
	b.Skip() // want `ERC008:.*Skip`
}

func BenchmarkWithReason(b *testing.B) {
	b.Skip("allocation-bound, meaningless under race")
}

type fakeT struct{}

func (fakeT) Skip() {}

func TestLocalSkipIsNotATestSkip(t *testing.T) {
	var ft fakeT
	ft.Skip()
	_ = t
}

func doWork() error { return nil }
