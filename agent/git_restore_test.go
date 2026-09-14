package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

type restoreRepo struct {
	t    *testing.T
	app  *App
	root string
	repo string
}

func newRestoreRepo(t *testing.T, configPath string) *restoreRepo {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git not available")
	}
	root := t.TempDir()
	r := &restoreRepo{t: t, root: root}
	cfg := filepath.Join(root, configPath)
	if !strings.HasSuffix(configPath, ".yml") {
		if err := os.MkdirAll(cfg, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	r.app = &App{cfg: &Config{ConfigPath: cfg, BackupDir: filepath.Join(root, "backups")}}
	r.repo = r.app.gitRepoDir()
	if err := os.MkdirAll(r.repo, 0o755); err != nil {
		t.Fatal(err)
	}
	r.git("init", "-q")
	return r
}

func (r *restoreRepo) git(args ...string) string {
	r.t.Helper()
	cmd := exec.Command("git", append([]string{"-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"}, args...)...)
	cmd.Dir = r.repo
	cmd.Env = append(os.Environ(), "GIT_CONFIG_GLOBAL=/dev/null", "GIT_CONFIG_NOSYSTEM=1")
	out, err := cmd.CombinedOutput()
	if err != nil {
		r.t.Fatalf("git %v: %v %s", args, err, out)
	}
	return strings.TrimSpace(string(out))
}

func (r *restoreRepo) commit(files map[string]string) string {
	r.t.Helper()
	for name, body := range files {
		p := filepath.Join(r.repo, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			r.t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			r.t.Fatal(err)
		}
	}
	r.git("add", "-A")
	r.git("commit", "-q", "-m", "snapshot")
	return r.git("rev-parse", "HEAD")
}

func (r *restoreRepo) restore(sha string) *httptest.ResponseRecorder {
	w := httptest.NewRecorder()
	r.app.gitRestoreHandler(w, httptest.NewRequest(http.MethodPost, "/api/backup/git/restore/"+sha, nil), sha)
	return w
}

func readTrim(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return strings.TrimSpace(string(data))
}

func writeFile(t *testing.T, path, body string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestGitRestoreBringsBackEveryDynamicFile(t *testing.T) {
	r := newRestoreRepo(t, "dynamic")
	first := r.commit(map[string]string{"dynamic/a.yml": "a: 1\n", "dynamic/b.yml": "b: 1\n"})
	r.commit(map[string]string{"dynamic/b.yml": "b: 2\n"})
	writeFile(t, filepath.Join(r.app.cfg.ConfigPath, "a.yml"), "a: 9\n")
	writeFile(t, filepath.Join(r.app.cfg.ConfigPath, "b.yml"), "b: 9\n")
	if w := r.restore(first); w.Code != http.StatusOK {
		t.Fatalf("got %d %s", w.Code, w.Body.String())
	}
	if got := readTrim(t, filepath.Join(r.app.cfg.ConfigPath, "a.yml")); got != "a: 1" {
		t.Fatalf("a file the commit did not change was left at %q", got)
	}
	if got := readTrim(t, filepath.Join(r.app.cfg.ConfigPath, "b.yml")); got != "b: 1" {
		t.Fatalf("b.yml is %q", got)
	}
}

func TestGitRestoreNeverWritesStaticIntoDynamic(t *testing.T) {
	r := newRestoreRepo(t, "dynamic")
	r.app.cfg.StaticConfigPath = filepath.Join(r.root, "traefik.yml")
	writeFile(t, r.app.cfg.StaticConfigPath, "old: true\n")
	sha := r.commit(map[string]string{"dynamic/a.yml": "a: 1\n", "static/traefik.yml": "entryPoints: {}\n"})
	if w := r.restore(sha); w.Code != http.StatusOK {
		t.Fatalf("got %d %s", w.Code, w.Body.String())
	}
	if _, err := os.Stat(filepath.Join(r.app.cfg.ConfigPath, "traefik.yml")); err == nil {
		t.Fatal("the static config was written into the dynamic config directory")
	}
	if got := readTrim(t, r.app.cfg.StaticConfigPath); got != "entryPoints: {}" {
		t.Fatalf("static config is %q", got)
	}
}

func TestGitRestoreSingleFileUsesItsOwnName(t *testing.T) {
	r := newRestoreRepo(t, "dynamic.yml")
	writeFile(t, r.app.cfg.ConfigPath, "x: 0\n")
	sha := r.commit(map[string]string{"dynamic/dynamic.yml": "x: 1\n", "dynamic/other.yml": "y: 2\n", "static/traefik.yml": "s: 3\n"})
	if w := r.restore(sha); w.Code != http.StatusOK {
		t.Fatalf("got %d %s", w.Code, w.Body.String())
	}
	if got := readTrim(t, r.app.cfg.ConfigPath); got != "x: 1" {
		t.Fatalf("the single config file holds %q, another file from the commit overwrote it", got)
	}
}

func TestGitRestoreReadsTheLegacyRootLayout(t *testing.T) {
	r := newRestoreRepo(t, "dynamic.yml")
	writeFile(t, r.app.cfg.ConfigPath, "x: 0\n")
	sha := r.commit(map[string]string{"dynamic.yml": "x: 3\n"})
	if w := r.restore(sha); w.Code != http.StatusOK {
		t.Fatalf("got %d %s", w.Code, w.Body.String())
	}
	if got := readTrim(t, r.app.cfg.ConfigPath); got != "x: 3" {
		t.Fatalf("got %q", got)
	}
}

func TestGitRestoreSkipsNestedAndNonConfigFiles(t *testing.T) {
	r := newRestoreRepo(t, "dynamic")
	sha := r.commit(map[string]string{"dynamic/a.yml": "a: 1\n", "dynamic/sub/x.yml": "x: 1\n", "dynamic/notes.md": "hi\n"})
	if w := r.restore(sha); w.Code != http.StatusOK {
		t.Fatalf("got %d %s", w.Code, w.Body.String())
	}
	entries, _ := os.ReadDir(r.app.cfg.ConfigPath)
	var names []string
	for _, e := range entries {
		names = append(names, e.Name())
	}
	if strings.Join(names, ",") != "a.yml" {
		t.Fatalf("restore wrote %v", names)
	}
}

func TestGitRestoreUnknownCommitTakesNoBackup(t *testing.T) {
	r := newRestoreRepo(t, "dynamic")
	r.commit(map[string]string{"dynamic/a.yml": "a: 1\n"})
	if w := r.restore("deadbeef"); w.Code != http.StatusNotFound {
		t.Fatalf("got %d %s", w.Code, w.Body.String())
	}
	entries, _ := os.ReadDir(r.app.cfg.BackupDir)
	for _, e := range entries {
		if e.Name() != "git-repo" {
			t.Fatalf("a backup was taken for a commit that does not exist: %s", e.Name())
		}
	}
}

func TestGitRestoreReportsAWriteFailure(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root ignores directory permissions")
	}
	r := newRestoreRepo(t, "dynamic")
	sha := r.commit(map[string]string{"dynamic/a.yml": "a: 1\n"})
	if err := os.Chmod(r.app.cfg.ConfigPath, 0o555); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chmod(r.app.cfg.ConfigPath, 0o755) })
	w := r.restore(sha)
	if w.Code != http.StatusInternalServerError || !strings.Contains(w.Body.String(), "dynamic/a.yml") {
		t.Fatalf("a failed write must not report success, got %d %s", w.Code, w.Body.String())
	}
}
