package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func withCredentialHelper(t *testing.T, stored string) (string, string) {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git is not installed")
	}
	dir := t.TempDir()
	store := filepath.Join(dir, "git-credentials")
	if stored != "" {
		if err := os.WriteFile(store, []byte(stored+"\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	cfg := filepath.Join(dir, "gitconfig")
	if err := os.WriteFile(cfg, []byte("[credential]\n\thelper = store --file "+store+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GIT_CONFIG_GLOBAL", cfg)
	t.Setenv("GIT_CONFIG_NOSYSTEM", "1")
	return dir, store
}

func TestGitRunIgnoresCredentialHelpers(t *testing.T) {
	sink := newAuthSink(t)
	dir, _ := withCredentialHelper(t, strings.Replace(sink.srv.URL, "http://", "http://operator:ghp_FROM_A_HELPER@", 1))
	app := &App{cfg: &Config{BackupDir: dir}}
	app.gitRun([]string{"ls-remote", "--", sink.srv.URL + "/other.git"}, dir)
	if got := sink.captured(); len(got) != 0 {
		t.Fatalf("a token stored by a credential helper on the machine was sent: %v", got)
	}
}

func TestGitRunDoesNotSaveTokensIntoAHelper(t *testing.T) {
	sink := newAuthSink(t)
	dir, store := withCredentialHelper(t, "")
	app := &App{cfg: &Config{BackupDir: dir}}
	app.gitRun([]string{"ls-remote", "--", sink.srv.URL + "/config.git"}, dir, gitCreds{username: "operator", token: "ghp_CONFIGURED"})
	if len(sink.captured()) == 0 {
		t.Fatal("the configured token must still be sent")
	}
	if data, err := os.ReadFile(store); err == nil && strings.Contains(string(data), "ghp_CONFIGURED") {
		t.Fatal("the agent's git token was saved into the machine credential store")
	}
}
