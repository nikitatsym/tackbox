package retainederrors

import (
	"reflect"
	"testing"

	"github.com/nikitatsym/tackbox/go/internal/astutil"
)

func TestWrappingArguments(t *testing.T) {
	for _, tc := range []struct {
		format string
		want   []int
	}{
		{"%w", []int{0}},
		{"context %s: %w (other: %v)", []int{1}},
		{"%% %w %w", []int{0, 1}},
		{"%+v %08w", []int{1}},
		{"%v", nil},
		{"%[1]w", []int{0}},
		{"%*w", []int{1}},
		{"%", []int{}},
	} {
		t.Run(tc.format, func(t *testing.T) {
			if got := astutil.WrappingArguments(tc.format); !reflect.DeepEqual(got, tc.want) {
				t.Fatalf("wrapped indexes = %v, want %v", got, tc.want)
			}
		})
	}
}
