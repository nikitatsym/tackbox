// Package report is a testdata stub matching the capture package import path
// and surface so callee type-info resolves for tier-1 recognition.
package report

func Error(area, msg string, err error, tags map[string]string, key string) {}

func Warn(area, msg string, err error, tags map[string]string, key string) {}

func Panic(name string, recovered any) {}

func Flush() {}
