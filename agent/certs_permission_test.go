package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCertsUnreadableStoreNamesTheOwner(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root reads a mode 000 file")
	}
	path := filepath.Join(t.TempDir(), "acme.json")
	writeACME(t, path, "le", "a.example.com")
	if err := os.Chmod(path, 0o000); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chmod(path, 0o600) })

	msg, _ := certsFor(t, path)["error"].(string)
	if !strings.Contains(msg, "Permission denied reading "+path) || !strings.Contains(msg, "user that owns it") {
		t.Fatalf("got %q, want the ownership hint", msg)
	}
	if strings.Contains(msg, "not found") || strings.Contains(msg, "chmod o+r") {
		t.Fatalf("got %q, a missing file or chmod hint sends the user the wrong way", msg)
	}
}
