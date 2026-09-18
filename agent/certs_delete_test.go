package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

func writeStore(t *testing.T, path string, domains ...string) {
	t.Helper()
	certs := []any{}
	for _, d := range domains {
		certs = append(certs, map[string]any{"domain": map[string]any{"main": d}, "certificate": "C", "key": "K"})
	}
	b, _ := json.Marshal(map[string]any{"le": map[string]any{"Account": map[string]any{"Email": "a@b.c"}, "Certificates": certs}})
	if err := os.WriteFile(path, b, 0o600); err != nil {
		t.Fatal(err)
	}
}

func storeDomains(t *testing.T, path string) []string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var store map[string]struct {
		Certificates []struct {
			Domain struct {
				Main string `json:"main"`
			} `json:"domain"`
		} `json:"Certificates"`
	}
	if err := json.Unmarshal(bytes.TrimSpace(raw), &store); err != nil {
		t.Fatalf("store is not valid JSON: %v (%q)", err, raw)
	}
	out := []string{}
	for _, c := range store["le"].Certificates {
		out = append(out, c.Domain.Main)
	}
	return out
}

func deleteApp(dir string, paths ...string) *App {
	return &App{cfg: &Config{
		ACMEJSONPath:   strings.Join(paths, ","),
		RestartMethod:  "poison-pill",
		SignalFilePath: filepath.Join(dir, "restart.sig"),
		BackupDir:      dir,
	}}
}

func postDelete(a *App, domains ...string) *httptest.ResponseRecorder {
	certs := []map[string]string{}
	for _, d := range domains {
		certs = append(certs, map[string]string{"resolver": "le", "main": d})
	}
	buf, _ := json.Marshal(map[string]any{"certs": certs})
	w := httptest.NewRecorder()
	a.certsDeleteHandler(w, httptest.NewRequest(http.MethodPost, "/api/traefik/certs/delete", bytes.NewReader(buf)))
	return w
}

func backupsIn(t *testing.T, dir string) []string {
	t.Helper()
	found, _ := filepath.Glob(filepath.Join(dir, "backups", "*.bak"))
	return found
}

func TestCertsDeleteInvalidSecondStoreChangesNothing(t *testing.T) {
	dir := t.TempDir()
	good := filepath.Join(dir, "good.json")
	bad := filepath.Join(dir, "bad.json")
	writeStore(t, good, "a.example.com", "b.example.com")
	if err := os.WriteFile(bad, []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	before, _ := os.ReadFile(good)
	w := postDelete(deleteApp(dir, good, bad), "a.example.com")
	if w.Code != http.StatusInternalServerError {
		t.Fatalf("got %d: %s", w.Code, w.Body.String())
	}
	after, _ := os.ReadFile(good)
	if !bytes.Equal(before, after) {
		t.Fatal("the first store was edited before the second was found to be broken")
	}
	if _, err := os.Stat(filepath.Join(dir, "restart.sig")); err == nil {
		t.Fatal("Traefik was restarted although nothing changed")
	}
	if got := backupsIn(t, dir); len(got) != 0 {
		t.Fatalf("backups were written for a refused removal: %v", got)
	}
}

func TestCertsDeleteReadOnlySecondStoreChangesNothing(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root can write a read-only file")
	}
	dir := t.TempDir()
	good := filepath.Join(dir, "good.json")
	locked := filepath.Join(dir, "locked.json")
	writeStore(t, good, "a.example.com")
	writeStore(t, locked, "a.example.com")
	if err := os.Chmod(locked, 0o400); err != nil {
		t.Fatal(err)
	}
	before, _ := os.ReadFile(good)
	w := postDelete(deleteApp(dir, good, locked), "a.example.com")
	if w.Code != http.StatusForbidden {
		t.Fatalf("got %d: %s", w.Code, w.Body.String())
	}
	after, _ := os.ReadFile(good)
	if !bytes.Equal(before, after) {
		t.Fatal("the writable store was edited before the read-only one was refused")
	}
}

func TestAcmeCommitRefusesAChangedStore(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "acme.json")
	writeStore(t, path, "a.example.com", "b.example.com")
	count, raw, body, err := acmePlan(path, map[string]bool{"le\x00a.example.com": true})
	if err != nil || count != 1 {
		t.Fatalf("plan: %d %v", count, err)
	}
	writeStore(t, path, "a.example.com", "b.example.com", "renewed.example.com")
	_, err = acmeCommit(path, raw, body, &App{cfg: &Config{BackupDir: dir}})
	if _, changed := err.(acmeChangedError); !changed {
		t.Fatalf("expected a changed-store error, got %v", err)
	}
	if got := storeDomains(t, path); len(got) != 3 {
		t.Fatalf("the renewed store was overwritten: %v", got)
	}
}

func TestAcmeApplyPlansAgainWhenTheStoreMoved(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "acme.json")
	writeStore(t, path, "a.example.com", "b.example.com")
	stale, _ := os.ReadFile(path)
	writeStore(t, path, "a.example.com", "b.example.com", "renewed.example.com")
	count, _, err := acmeApply(path, map[string]bool{"le\x00a.example.com": true}, stale, &App{cfg: &Config{BackupDir: dir}})
	if err != nil || count != 1 {
		t.Fatalf("apply: %d %v", count, err)
	}
	got := storeDomains(t, path)
	if len(got) != 2 || got[0] != "b.example.com" || got[1] != "renewed.example.com" {
		t.Fatalf("the renewal was lost or the removal skipped: %v", got)
	}
}

func TestCertsDeleteConcurrentRemovalsKeepBoth(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "acme.json")
	writeStore(t, path, "x.example.com", "y.example.com", "z.example.com")
	app := deleteApp(dir, path)
	var wg sync.WaitGroup
	codes := make([]int, 2)
	for i, d := range []string{"x.example.com", "y.example.com"} {
		wg.Add(1)
		go func(i int, d string) {
			defer wg.Done()
			codes[i] = postDelete(app, d).Code
		}(i, d)
	}
	wg.Wait()
	for _, c := range codes {
		if c != http.StatusOK {
			t.Fatalf("a removal failed: %v", codes)
		}
	}
	if got := storeDomains(t, path); len(got) != 1 || got[0] != "z.example.com" {
		t.Fatalf("one removal overwrote the other: %v", got)
	}
}

func TestAcmeWriteInPlaceShrinksCleanly(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "acme.json")
	long := []byte(`{"le":{"Certificates":[{"domain":{"main":"long.example.com"}}]}}`)
	if err := os.WriteFile(path, long, 0o644); err != nil {
		t.Fatal(err)
	}
	short := []byte(`{"le":{}}`)
	if err := acmeWriteInPlace(path, short, long); err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(path)
	if !bytes.Equal(got, short) {
		t.Fatalf("got %q", got)
	}
	if info, _ := os.Stat(path); info.Mode().Perm() != 0o600 {
		t.Fatalf("mode %v", info.Mode().Perm())
	}
}

func TestAcmeBackupNamesDoNotCollide(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "acme.json")
	writeStore(t, path, "a.example.com")
	raw, _ := os.ReadFile(path)
	app := &App{cfg: &Config{BackupDir: dir}}
	first, err := acmeBackupBytes(path, raw, app)
	if err != nil {
		t.Fatal(err)
	}
	second, err := acmeBackupBytes(path, raw, app)
	if err != nil {
		t.Fatal(err)
	}
	if first == second {
		t.Fatal("two backups in the same second share a name, so the second overwrote the first")
	}
	for _, p := range []string{first, second} {
		info, err := os.Stat(p)
		if err != nil || info.Mode().Perm() != 0o600 {
			t.Fatalf("backup %s missing or loose: %v", p, err)
		}
	}
}
