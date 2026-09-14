package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSafeBaseName(t *testing.T) {
	for _, name := range []string{"dynamic.yml", "my routes.yaml", "app-1.yml", "a.b.yml"} {
		if _, ok := safeBaseName(name); !ok {
			t.Fatalf("%q should be accepted", name)
		}
	}
	for _, name := range []string{"", ".", "..", "../x", "a/b", "a\\b", "x\x00y", "..hidden", "a..b.yml"} {
		if _, ok := safeBaseName(name); ok {
			t.Fatalf("%q should be refused", name)
		}
	}
}

func TestCreateFileBakRejectsUnsafeNames(t *testing.T) {
	root := t.TempDir()
	target := filepath.Join(root, "dynamic.yml")
	if err := os.WriteFile(target, []byte("http: {}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	a := &App{cfg: &Config{BackupDir: root}}
	for _, name := range []string{"../escape", "a/b", "..", ".", "a\\b", "x\x00y"} {
		if err := a.createFileBak(target, name); err == nil {
			t.Fatalf("%q was accepted as a backup name", name)
		}
	}
	entries, _ := os.ReadDir(root)
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".bak") {
			t.Fatalf("a backup landed outside the backup folder: %s", e.Name())
		}
	}
}

func TestCreateFileBakEmptyNameUsesTheTargetName(t *testing.T) {
	root := t.TempDir()
	target := filepath.Join(root, "dynamic.yml")
	if err := os.WriteFile(target, []byte("http: {}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	a := &App{cfg: &Config{BackupDir: root}}
	if err := a.createFileBak(target, ""); err != nil {
		t.Fatal(err)
	}
	if found, _ := filepath.Glob(filepath.Join(a.backupDir(), "dynamic.yml.*.bak")); len(found) != 1 {
		t.Fatalf("expected one backup named after the file, found %v", found)
	}
}

func TestSingleFileConfigWriteNamesTheBackupAfterTheFile(t *testing.T) {
	root := t.TempDir()
	cfg := filepath.Join(root, "dynamic.yml")
	if err := os.WriteFile(cfg, []byte("http: {}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	a := &App{cfg: &Config{ConfigPath: cfg, BackupDir: root}}
	body := `{"name":"../../escape","content":"http:\n  routers: {}\n"}`
	w := httptest.NewRecorder()
	a.configsWriteHandler(w, httptest.NewRequest(http.MethodPost, "/api/configs", strings.NewReader(body)))
	if w.Code != http.StatusOK {
		t.Fatalf("got %d: %s", w.Code, w.Body.String())
	}
	if found, _ := filepath.Glob(filepath.Join(a.backupDir(), "dynamic.yml.*.bak")); len(found) != 1 {
		t.Fatalf("the backup was not named after the config file: %v", found)
	}
	if escaped, _ := filepath.Glob(filepath.Join(filepath.Dir(root), "escape*")); len(escaped) > 0 {
		t.Fatalf("a client-supplied name wrote outside the backup folder: %v", escaped)
	}
}

func logsApp(t *testing.T, lines int) *App {
	t.Helper()
	path := filepath.Join(t.TempDir(), "access.log")
	var b strings.Builder
	for i := 0; i < lines; i++ {
		fmt.Fprintf(&b, "line %d\n", i)
	}
	if err := os.WriteFile(path, []byte(b.String()), 0o644); err != nil {
		t.Fatal(err)
	}
	return &App{cfg: &Config{AccessLogPath: path}}
}

func getLogs(a *App, query string) *httptest.ResponseRecorder {
	w := httptest.NewRecorder()
	a.logsHandler(w, httptest.NewRequest(http.MethodGet, "/api/traefik/logs"+query, nil))
	return w
}

func logLines(t *testing.T, w *httptest.ResponseRecorder) []string {
	t.Helper()
	var out struct {
		Lines []string `json:"lines"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &out); err != nil {
		t.Fatalf("bad json: %v (%s)", err, w.Body.String())
	}
	return out.Lines
}

func TestLogsRejectsInvalidLines(t *testing.T) {
	a := logsApp(t, 10)
	for _, q := range []string{"?lines=-1", "?lines=0", "?lines=abc", "?lines=5abc", "?lines="} {
		w := getLogs(a, q)
		if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "Invalid lines parameter") {
			t.Fatalf("%s: got %d %s", q, w.Code, w.Body.String())
		}
	}
}

func TestLogsDefaultsTo100(t *testing.T) {
	if n := len(logLines(t, getLogs(logsApp(t, 150), ""))); n != 100 {
		t.Fatalf("got %d lines", n)
	}
}

func TestLogsCapsAt1000(t *testing.T) {
	if n := len(logLines(t, getLogs(logsApp(t, 1200), "?lines=5000"))); n != 1000 {
		t.Fatalf("got %d lines", n)
	}
}
