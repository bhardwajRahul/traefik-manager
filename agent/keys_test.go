package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"testing"
	"time"
)

func settleKeyStore(ks *keyStore) {
	time.Sleep(20 * time.Millisecond)
	ks.saveMu.Lock()
	ks.saveMu.Unlock()
}

func TestKeyStoreDeleteIsNotUndoneByALastUsedSave(t *testing.T) {
	root := t.TempDir()
	for i := 0; i < 50; i++ {
		dir := filepath.Join(root, strconv.Itoa(i))
		ks := newKeyStore(dir)
		id, raw, err := ks.create("phone")
		if err != nil {
			t.Fatal(err)
		}
		ks.mu.Lock()
		ks.lastUsedSave = time.Time{}
		ks.mu.Unlock()
		if !ks.validate(raw) {
			t.Fatal("a fresh key was rejected")
		}
		if !ks.delete(id) {
			t.Fatal("the key could not be deleted")
		}
		settleKeyStore(ks)
		if newKeyStore(dir).validate(raw) {
			t.Fatalf("iteration %d: a revoked key came back after a restart", i)
		}
	}
}

func TestKeyStoreConcurrentUseLeavesAValidFile(t *testing.T) {
	dir := t.TempDir()
	ks := newKeyStore(dir)
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			id, raw, err := ks.create("temporary")
			if err != nil {
				return
			}
			ks.mu.Lock()
			ks.lastUsedSave = time.Time{}
			ks.mu.Unlock()
			ks.validate(raw)
			ks.delete(id)
		}()
	}
	for i := 0; i < 5; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, _, _ = ks.create("keep")
		}()
	}
	wg.Wait()
	settleKeyStore(ks)
	data, err := os.ReadFile(filepath.Join(dir, "api_keys.json"))
	if err != nil {
		t.Fatal(err)
	}
	var onDisk []APIKey
	if err := json.Unmarshal(data, &onDisk); err != nil {
		t.Fatalf("the key file is not valid JSON: %v", err)
	}
	if len(onDisk) != 5 {
		t.Fatalf("expected the 5 kept keys on disk, found %d", len(onDisk))
	}
	if n := len(newKeyStore(dir).list()); n != 5 {
		t.Fatalf("a reload found %d keys", n)
	}
}

func TestKeyStoreCorruptFileIsKeptAside(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "api_keys.json")
	if err := os.WriteFile(path, []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	ks := newKeyStore(dir)
	aside, _ := filepath.Glob(path + ".corrupt-*")
	if len(aside) != 1 {
		t.Fatalf("the corrupt key file was not kept aside: %v", aside)
	}
	if _, _, err := ks.create("new"); err != nil {
		t.Fatal(err)
	}
	if got, _ := os.ReadFile(aside[0]); string(got) != "{not json" {
		t.Fatal("creating a key overwrote the only copy of the old key file")
	}
}

func TestKeyStoreFileIsPrivate(t *testing.T) {
	dir := t.TempDir()
	if _, _, err := newKeyStore(dir).create("phone"); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(filepath.Join(dir, "api_keys.json"))
	if err != nil || info.Mode().Perm() != 0o600 {
		t.Fatalf("key file mode: %v %v", info.Mode().Perm(), err)
	}
}

func TestKeyStoreRevokedKeyStaysRevokedAfterReload(t *testing.T) {
	dir := t.TempDir()
	ks := newKeyStore(dir)
	id, raw, err := ks.create("phone")
	if err != nil {
		t.Fatal(err)
	}
	ks.delete(id)
	if newKeyStore(dir).validate(raw) {
		t.Fatal("a revoked key validated after a reload")
	}
}
