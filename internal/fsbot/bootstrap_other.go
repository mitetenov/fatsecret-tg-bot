//go:build !linux

package fsbot

import "os"

func Bootstrap(dir string) error { return os.MkdirAll(dir, 0700) }
