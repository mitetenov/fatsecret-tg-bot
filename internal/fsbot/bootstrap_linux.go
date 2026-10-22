//go:build linux

package fsbot

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"syscall"
)

// Bootstrap repairs existing Docker volumes before dropping all root privileges.
func Bootstrap(dir string) error {
	if e := os.MkdirAll(dir, 0700); e != nil {
		return e
	}
	if os.Geteuid() != 0 {
		return nil
	}
	root, e := os.Lstat(dir)
	if e != nil {
		return e
	}
	if !root.IsDir() || root.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("каталог состояния должен быть обычным каталогом")
	}
	e = filepath.WalkDir(dir, func(path string, d fs.DirEntry, e error) error {
		if e != nil {
			return e
		}
		info, e := d.Info()
		if e != nil {
			return e
		}
		stat, ok := info.Sys().(*syscall.Stat_t)
		if !ok || stat.Uid != 10001 || stat.Gid != 10001 {
			return os.Lchown(path, 10001, 10001)
		}
		return nil
	})
	if e != nil {
		return fmt.Errorf("не удалось назначить владельца состояния: %w", e)
	}
	if e = syscall.Setgroups([]int{}); e != nil {
		return e
	}
	if e = syscall.Setgid(10001); e != nil {
		return e
	}
	return syscall.Setuid(10001)
}
