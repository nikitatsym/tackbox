// Package report is a testdata stub matching the capture package import path
// and surface so callee type-info resolves for tier-1 recognition. Notify is
// the user-lane-only verb ERC009 gates.
package report

func Error(area, msg string, err error, tags map[string]string, key string) {}

func Warn(area, msg string, err error, tags map[string]string, key string) {}

func Notify(area, msg string, err error, tags map[string]string, key string) {}
