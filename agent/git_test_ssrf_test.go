package main

import (
	"net/http"
	"net/http/httptest"
	"os/exec"
	"strings"
	"sync/atomic"
	"testing"
)

func TestSSRFOKMatchesTheHostRules(t *testing.T) {
	cases := map[string]bool{
		"http://169.254.169.254/latest":  false,
		"http://[fe80::1]/repo.git":      false,
		"http://0.0.0.0/repo.git":        false,
		"http://240.0.0.1/repo.git":      false,
		"http://224.0.0.1/repo.git":      false,
		"http://127.0.0.1:3000/repo.git": true,
		"http://10.0.0.5/repo.git":       true,
		"ssh://192.168.1.10/repo.git":    true,
		"not a url":                      false,
	}
	for raw, want := range cases {
		if got := ssrfOK(raw); got != want {
			t.Fatalf("%s: got %v want %v", raw, got, want)
		}
	}
}

func TestGitTestRefusesLinkLocalTargets(t *testing.T) {
	a := gitTestApp(t, "")
	w := postGitTest(a, map[string]string{"repo_url": "http://169.254.169.254/latest.git"})
	if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "not allowed") {
		t.Fatalf("got %d %s", w.Code, w.Body.String())
	}
}

func TestGitTestDoesNotFollowRedirects(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git is not installed")
	}
	var hits int32
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&hits, 1)
		w.WriteHeader(http.StatusNotFound)
	}))
	defer target.Close()
	bounce := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL+r.URL.RequestURI(), http.StatusFound)
	}))
	defer bounce.Close()
	postGitTest(gitTestApp(t, ""), map[string]string{"repo_url": bounce.URL + "/repo.git"})
	if n := atomic.LoadInt32(&hits); n != 0 {
		t.Fatalf("git followed a redirect to another address %d times", n)
	}
}
