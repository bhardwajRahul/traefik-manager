package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"testing"
)

var restoredStore = []byte(`{"le":{"Account":{"Email":"a@b.c"},"Certificates":[{"domain":{"main":"restored.example.com"}}]}}`)

func restoreApp(t *testing.T, restart string, stores ...string) (*App, string) {
	t.Helper()
	root := t.TempDir()
	dyn := filepath.Join(root, "conf.d")
	if err := os.MkdirAll(dyn, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dyn, "dynamic.yml"), []byte("http: {}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	paths := []string{}
	for _, s := range stores {
		p := filepath.Join(root, s)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		writeStore(t, p, "live.example.com")
		paths = append(paths, p)
	}
	a := &App{cfg: &Config{
		ConfigPath:     dyn,
		BackupDir:      root,
		ACMEJSONPath:   strings.Join(paths, ","),
		RestartMethod:  restart,
		SignalFilePath: filepath.Join(root, "restart.sig"),
	}}
	if err := os.MkdirAll(a.backupDir(), 0o755); err != nil {
		t.Fatal(err)
	}
	return a, root
}

func writeBak(t *testing.T, a *App, name string, body []byte) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(a.backupDir(), name), body, 0o600); err != nil {
		t.Fatal(err)
	}
}

func postRestore(a *App, name string) *httptest.ResponseRecorder {
	w := httptest.NewRecorder()
	a.restoreHandler(w, httptest.NewRequest(http.MethodPost, "/api/restore/"+name, nil))
	return w
}

func inodeOf(t *testing.T, path string) uint64 {
	t.Helper()
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	return uint64(info.Sys().(*syscall.Stat_t).Ino)
}

func TestAcmeBackupKeyVectors(t *testing.T) {
	cases := []struct{ paths, keys []string }{
		{[]string{"/a/acme.json"}, []string{"acme.json"}},
		{[]string{"/le/a/acme.json", "/le/b/acme.json"}, []string{"a-acme.json", "b-acme.json"}},
		{[]string{"/srv/ovh.json", "/srv/lan.json"}, []string{"ovh.json", "lan.json"}},
		{[]string{"/one/le@prod/acme.json", "/two/acme.json"}, []string{"le-prod-acme.json", "two-acme.json"}},
		{[]string{"acme.json", "/x/acme.json"}, []string{"store-acme.json", "x-acme.json"}},
	}
	for _, c := range cases {
		for i, p := range c.paths {
			if got := acmeBackupKey(p, c.paths); got != c.keys[i] {
				t.Fatalf("key for %s among %v: got %q want %q", p, c.paths, got, c.keys[i])
			}
		}
	}
	paths := []string{"/x/certs/acme.json", "/y/certs/acme.json"}
	for _, p := range paths {
		sum := sha256.Sum256([]byte(p))
		want := hex.EncodeToString(sum[:])[:8] + "-acme.json"
		if got := acmeBackupKey(p, paths); got != want {
			t.Fatalf("hash fallback for %s: got %q want %q", p, got, want)
		}
	}
}

func TestBackupListClassifiesCertBackups(t *testing.T) {
	a, _ := restoreApp(t, "poison-pill", "le/a/acme.json", "le/b/acme.json")
	for _, n := range []string{"a-acme.json.20260914_101010.bak", "acme.json.20260914_101010.bak", "dynamic.yml.20260914_101010.bak"} {
		writeBak(t, a, n, []byte("{}"))
	}
	rec := httptest.NewRecorder()
	a.backupsListHandler(rec, httptest.NewRequest(http.MethodGet, "/api/backups", nil))
	var resp struct {
		Backups []struct {
			Name string `json:"name"`
			Kind string `json:"kind"`
		} `json:"backups"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	kinds := map[string]string{}
	for _, b := range resp.Backups {
		kinds[b.Name] = b.Kind
	}
	if kinds["a-acme.json.20260914_101010.bak"] != "certs" || kinds["acme.json.20260914_101010.bak"] != "certs" {
		t.Fatalf("certificate backups were not classified: %v", kinds)
	}
	if kinds["dynamic.yml.20260914_101010.bak"] != "routes" {
		t.Fatalf("a config backup was misclassified: %v", kinds)
	}
}

func TestRestoreCertBackupWritesInPlaceToItsStore(t *testing.T) {
	a, root := restoreApp(t, "poison-pill", "le/a/acme.json", "le/b/acme.json")
	first := filepath.Join(root, "le/a/acme.json")
	second := filepath.Join(root, "le/b/acme.json")
	beforeFirst, _ := os.ReadFile(first)
	inode := inodeOf(t, second)
	writeBak(t, a, "b-acme.json.20260914_101010.bak", restoredStore)

	w := postRestore(a, "b-acme.json.20260914_101010.bak")
	if w.Code != http.StatusOK {
		t.Fatalf("got %d: %s", w.Code, w.Body.String())
	}
	var body map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil || body["restarted"] != true {
		t.Fatalf("expected restarted:true, got %s", w.Body.String())
	}
	if got := storeDomains(t, second); len(got) != 1 || got[0] != "restored.example.com" {
		t.Fatalf("the store was not restored: %v", got)
	}
	if afterFirst, _ := os.ReadFile(first); !bytes.Equal(beforeFirst, afterFirst) {
		t.Fatal("the other store with the same file name was overwritten")
	}
	if inodeOf(t, second) != inode {
		t.Fatal("the store was replaced instead of rewritten, which detaches a bind mount")
	}
	if info, _ := os.Stat(second); info.Mode().Perm() != 0o600 {
		t.Fatalf("mode %v", info.Mode().Perm())
	}
	if _, err := os.Stat(filepath.Join(root, "conf.d", "acme.json")); err == nil {
		t.Fatal("the private keys were written into the dynamic config directory")
	}
	if _, err := os.Stat(filepath.Join(root, "restart.sig")); err != nil {
		t.Fatal("Traefik was not restarted, so the restore would be undone")
	}
	pre, _ := filepath.Glob(filepath.Join(a.backupDir(), "b-acme.json.*.bak"))
	if len(pre) < 2 {
		t.Fatalf("no pre-restore backup of the store was taken: %v", pre)
	}
}

func TestRestoreCertBackupRefusedWithoutRestartMethod(t *testing.T) {
	a, root := restoreApp(t, "", "acme.json")
	store := filepath.Join(root, "acme.json")
	before, _ := os.ReadFile(store)
	writeBak(t, a, "acme.json.20260914_101010.bak", restoredStore)
	if w := postRestore(a, "acme.json.20260914_101010.bak"); w.Code != http.StatusForbidden {
		t.Fatalf("got %d: %s", w.Code, w.Body.String())
	}
	if after, _ := os.ReadFile(store); !bytes.Equal(before, after) {
		t.Fatal("the store was written although Traefik could not be restarted")
	}
	if _, err := os.Stat(filepath.Join(root, "conf.d", "acme.json")); err == nil {
		t.Fatal("the backup fell through into the dynamic config directory")
	}
}

func TestRestoreCertBackupRejectsInvalidJSON(t *testing.T) {
	a, root := restoreApp(t, "poison-pill", "acme.json")
	store := filepath.Join(root, "acme.json")
	before, _ := os.ReadFile(store)
	writeBak(t, a, "acme.json.20260914_101010.bak", []byte("{not json"))
	if w := postRestore(a, "acme.json.20260914_101010.bak"); w.Code != http.StatusBadRequest {
		t.Fatalf("got %d: %s", w.Code, w.Body.String())
	}
	if after, _ := os.ReadFile(store); !bytes.Equal(before, after) {
		t.Fatal("a truncated backup emptied the store")
	}
}

func TestRestoreAmbiguousCertBackupRefused(t *testing.T) {
	a, root := restoreApp(t, "poison-pill", "le/a/acme.json", "le/b/acme.json")
	first, _ := os.ReadFile(filepath.Join(root, "le/a/acme.json"))
	second, _ := os.ReadFile(filepath.Join(root, "le/b/acme.json"))
	writeBak(t, a, "acme.json.20260914_101010.bak", restoredStore)
	if w := postRestore(a, "acme.json.20260914_101010.bak"); w.Code != http.StatusConflict {
		t.Fatalf("got %d: %s", w.Code, w.Body.String())
	}
	a1, _ := os.ReadFile(filepath.Join(root, "le/a/acme.json"))
	b1, _ := os.ReadFile(filepath.Join(root, "le/b/acme.json"))
	if !bytes.Equal(first, a1) || !bytes.Equal(second, b1) {
		t.Fatal("an ambiguous backup was restored into a store anyway")
	}
}

func TestRestoreUnknownNameRejectedInDirMode(t *testing.T) {
	a, root := restoreApp(t, "poison-pill")
	writeBak(t, a, "notes.txt.20260914_101010.bak", []byte("x"))
	if w := postRestore(a, "notes.txt.20260914_101010.bak"); w.Code != http.StatusBadRequest {
		t.Fatalf("got %d: %s", w.Code, w.Body.String())
	}
	if _, err := os.Stat(filepath.Join(root, "conf.d", "notes.txt")); err == nil {
		t.Fatal("a file that is not dynamic config was written into the config directory")
	}
}

func TestRestoreConfigBackupStillWorks(t *testing.T) {
	a, root := restoreApp(t, "poison-pill")
	writeBak(t, a, "dynamic.yml.20260914_101010.bak", []byte("http:\n  routers: {}\n"))
	if w := postRestore(a, "dynamic.yml.20260914_101010.bak"); w.Code != http.StatusOK {
		t.Fatalf("got %d: %s", w.Code, w.Body.String())
	}
	got, _ := os.ReadFile(filepath.Join(root, "conf.d", "dynamic.yml"))
	if string(got) != "http:\n  routers: {}\n" {
		t.Fatalf("got %q", got)
	}
}

func TestRestoreSingleFileModeRoutesCertBackupToTheStore(t *testing.T) {
	root := t.TempDir()
	cfg := filepath.Join(root, "dynamic.yml")
	if err := os.WriteFile(cfg, []byte("http: {}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	store := filepath.Join(root, "acme.json")
	writeStore(t, store, "live.example.com")
	a := &App{cfg: &Config{ConfigPath: cfg, BackupDir: root, ACMEJSONPath: store, RestartMethod: "poison-pill", SignalFilePath: filepath.Join(root, "restart.sig")}}
	if err := os.MkdirAll(a.backupDir(), 0o755); err != nil {
		t.Fatal(err)
	}
	writeBak(t, a, "acme.json.20260914_101010.bak", restoredStore)
	if w := postRestore(a, "acme.json.20260914_101010.bak"); w.Code != http.StatusOK {
		t.Fatalf("got %d: %s", w.Code, w.Body.String())
	}
	if got, _ := os.ReadFile(cfg); string(got) != "http: {}\n" {
		t.Fatalf("the single config file was overwritten with the certificate store: %q", got)
	}
	if got := storeDomains(t, store); len(got) != 1 || got[0] != "restored.example.com" {
		t.Fatalf("the store was not restored: %v", got)
	}
}

func TestGitStageCopiesOnlyDynamicConfig(t *testing.T) {
	root := t.TempDir()
	conf := filepath.Join(root, "conf.d")
	if err := os.MkdirAll(conf, 0o755); err != nil {
		t.Fatal(err)
	}
	for name, body := range map[string]string{
		"dynamic.yml":   "http: {}\n",
		"extra.toml":    "[http]\n",
		"acme.json":     `{"le":{}}`,
		"notes.txt":     "x",
		"draft.yml.tmp": "x",
		"old.yml.bak":   "x",
	} {
		if err := os.WriteFile(filepath.Join(conf, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	static := filepath.Join(root, "traefik.yml")
	if err := os.WriteFile(static, []byte("entryPoints: {}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	a := &App{cfg: &Config{ConfigPath: conf, StaticConfigPath: static, ACMEJSONPath: filepath.Join(conf, "acme.json")}}
	dyn := filepath.Join(root, "repo", "dynamic")
	st := filepath.Join(root, "repo", "static")
	a.gitStage(dyn, st)
	entries, _ := os.ReadDir(dyn)
	names := []string{}
	for _, e := range entries {
		names = append(names, e.Name())
	}
	sort.Strings(names)
	if strings.Join(names, ",") != "dynamic.yml,extra.toml" {
		t.Fatalf("git would commit files that are not dynamic config: %v", names)
	}
	if _, err := os.Stat(filepath.Join(st, "traefik.yml")); err != nil {
		t.Fatal("the static config must still be committed")
	}
}
