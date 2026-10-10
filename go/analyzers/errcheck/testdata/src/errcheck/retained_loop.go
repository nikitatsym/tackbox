package errcheck

import (
	"errors"
	"sync"
)

func appendLoopFailures(inputs []func() error) error {
	var failures []error
	for _, input := range inputs {
		err := input()
		if err != nil {
			failures = append(failures, err)
			continue
		}
	}
	return errors.Join(failures...)
}

func joinLoopFailures(inputs []func() error) error {
	var joined error
	for _, input := range inputs {
		err := input()
		if err != nil {
			joined = errors.Join(joined, err)
			continue
		}
	}
	return joined
}

func discardedLoopFailures(inputs []func() error) int {
	var failures []error
	for _, input := range inputs {
		err := input()
		if err != nil { // want `ERC001:.*err=err`
			failures = append(failures, err)
			continue
		}
	}
	return len(failures)
}

func resetLoopFailures(inputs []func() error) error {
	var failures []error
	for _, input := range inputs {
		err := input()
		if err != nil { // want `ERC001:.*err=err`
			failures = append(failures, err)
			continue
		}
		failures = nil
	}
	return errors.Join(failures...)
}

func resetJoinedLoopFailures(inputs []func() error) error {
	var joined error
	for _, input := range inputs {
		err := input()
		if err != nil { // want `ERC001:.*err=err`
			joined = errors.Join(joined, err)
			continue
		}
		joined = nil
	}
	return joined
}

type nativeItem struct {
	Name string
	Type string
}

type nativeSource struct {
	mu          sync.Mutex
	items       []nativeItem
	fingerprint func(string) (string, error)
}

func (s *nativeSource) Fingerprints() (map[string]string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make(map[string]string, len(s.items))
	var failures []error
	for _, it := range s.items {
		if it.Type != "ssh" {
			continue
		}
		fp, err := s.fingerprint(it.Name)
		if err != nil {
			failures = append(failures, err)
			continue
		}
		out[it.Name] = fp
	}
	return out, errors.Join(failures...)
}
