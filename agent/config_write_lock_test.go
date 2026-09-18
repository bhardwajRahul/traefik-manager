package main

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"gopkg.in/yaml.v3"
)

func leftoverTemps(t *testing.T, dir string) []string {
	t.Helper()
	var out []string
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		if strings.Contains(e.Name(), ".tmp") {
			out = append(out, e.Name())
		}
	}
	return out
}

func TestAtomicWriteConcurrentWritersLeaveOneWholeFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "dynamic.yml")
	contents := map[string]bool{}
	var wg sync.WaitGroup
	for i := 0; i < 40; i++ {
		body := fmt.Sprintf("http:\n  routers:\n    r%d:\n      rule: Host(`r%d.example.com`)\n", i, i) + strings.Repeat("# pad\n", 200)
		contents[body] = true
		wg.Add(1)
		go func(b string) {
			defer wg.Done()
			if err := atomicWrite(path, []byte(b)); err != nil {
				t.Error(err)
			}
		}(body)
	}
	wg.Wait()
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !contents[string(got)] {
		t.Fatal("the file holds a mix of two writes")
	}
	if left := leftoverTemps(t, dir); len(left) > 0 {
		t.Fatalf("temporary files were left behind: %v", left)
	}
	if info, _ := os.Stat(path); info.Mode().Perm() != 0o644 {
		t.Fatalf("mode %v", info.Mode().Perm())
	}
}

func TestConcurrentConfigWritesKeepTheFileValid(t *testing.T) {
	root := t.TempDir()
	cfgDir := filepath.Join(root, "dynamic")
	if err := os.MkdirAll(cfgDir, 0o755); err != nil {
		t.Fatal(err)
	}
	a := &App{cfg: &Config{ConfigPath: cfgDir, BackupDir: filepath.Join(root, "backups")}}
	var wg sync.WaitGroup
	for i := 0; i < 30; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			content := fmt.Sprintf("http:\n  routers:\n    r%d:\n      rule: Host(`r%d.example.com`)\n      service: s%d\n", i, i, i)
			body := fmt.Sprintf(`{"name":"routes.yml","content":%q}`, content)
			w := httptest.NewRecorder()
			a.configsWriteHandler(w, httptest.NewRequest(http.MethodPost, "/api/configs", strings.NewReader(body)))
			if w.Code != http.StatusOK {
				t.Errorf("write %d: %d %s", i, w.Code, w.Body.String())
			}
		}(i)
	}
	wg.Wait()
	data, err := os.ReadFile(filepath.Join(cfgDir, "routes.yml"))
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if err := yaml.Unmarshal(data, &parsed); err != nil || parsed["http"] == nil {
		t.Fatalf("the config is not whole YAML after concurrent writes: %v\n%s", err, data)
	}
	if left := leftoverTemps(t, cfgDir); len(left) > 0 {
		t.Fatalf("temporary files were left in the config directory: %v", left)
	}
}
