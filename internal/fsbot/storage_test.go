package fsbot

import (
	"context"
	"database/sql"
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func openTestStore(t *testing.T) *Store {
	t.Helper()
	s, e := OpenStore(filepath.Join(t.TempDir(), "state.sqlite3"))
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { s.Close() })
	return s
}
func TestStoreLegacyMigration(t *testing.T) {
	path := filepath.Join(t.TempDir(), "old.sqlite3")
	db, e := sql.Open("sqlite", path)
	if e != nil {
		t.Fatal(e)
	}
	_, e = db.Exec(`CREATE TABLE drafts(draft_id INTEGER PRIMARY KEY,user_id INTEGER,payload TEXT,created_at INTEGER);CREATE TABLE batches(batch_id INTEGER PRIMARY KEY,user_id INTEGER,entry_ids TEXT,created_at INTEGER);`)
	if e != nil {
		t.Fatal(e)
	}
	b, _ := json.Marshal(readyDraft())
	_, e = db.Exec(`INSERT INTO drafts VALUES(1,1,?,0)`, string(b))
	if e != nil {
		t.Fatal(e)
	}
	if _, e = db.Exec(`INSERT INTO batches VALUES(1,1,'["old-entry"]',0)`); e != nil {
		t.Fatal(e)
	}
	db.Close()
	s, e := OpenStore(path)
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	d, e := s.Draft(1, 1)
	if e != nil || d.Items[0].Name != "A" {
		t.Fatal(d, e)
	}
	if d, e = s.Draft(1, 2); e != nil || d != nil {
		t.Fatal("ownership", d, e)
	}
	if d, e = s.ClaimDraft(1, 1); e != nil || d == nil {
		t.Fatal(d, e)
	}
	s.ReleaseDraft(1, 1)
	_, ids, e := s.ClaimBatch(1)
	if e != nil || len(ids) != 1 || ids[0] != "old-entry" {
		t.Fatal(ids, e)
	}
}
func TestStoreCrossConnectionClaims(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.sqlite3")
	a, e := OpenStore(path)
	if e != nil {
		t.Fatal(e)
	}
	defer a.Close()
	id, e := a.SaveDraft(1, readyDraft())
	if e != nil {
		t.Fatal(e)
	}
	b, e := OpenStore(path)
	if e != nil {
		t.Fatal(e)
	}
	defer b.Close()
	var won atomic.Int32
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		s := a
		if i%2 == 1 {
			s = b
		}
		wg.Go(func() {
			d, e := s.ClaimDraft(id, 1)
			if e != nil {
				t.Error(e)
			}
			if d != nil {
				won.Add(1)
			}
		})
	}
	wg.Wait()
	if won.Load() != 1 {
		t.Fatal("claims", won.Load())
	}
	if d, e := b.ClaimDraft(id, 2); e != nil || d != nil {
		t.Fatal("wrong owner", d, e)
	}
	c, e := OpenStore(path)
	if e != nil {
		t.Fatal(e)
	}
	defer c.Close()
	if d, e := c.ClaimDraft(id, 1); e != nil || d != nil {
		t.Fatal("opening store released fresh claim")
	}
	if e = a.ReleaseDraft(id, 1); e != nil {
		t.Fatal(e)
	}
	if d, e := b.ClaimDraft(id, 1); e != nil || d == nil {
		t.Fatal(d, e)
	}
}
func TestStoreWriteUndoMutualExclusion(t *testing.T) {
	s := openTestStore(t)
	d := readyDraft()
	id, e := s.SaveDraft(1, d)
	if e != nil {
		t.Fatal(e)
	}
	d.Items[0].Status = "written"
	d.Items[0].EntryID = "a"
	if e = s.RecordWrite(id, 1, d, "a"); e != nil {
		t.Fatal(e)
	}
	if e = s.RecordWrite(id, 1, d, "a"); e != nil {
		t.Fatal(e)
	}
	if _, e = s.ClaimDraft(id, 1); e != nil {
		t.Fatal(e)
	}
	if batch, _, e := s.ClaimBatch(1); e != nil || batch != 0 {
		t.Fatal("undo while writing", batch, e)
	}
	s.ReleaseDraft(id, 1)
	batch, ids, e := s.ClaimBatch(1)
	if e != nil || len(ids) != 1 {
		t.Fatal(ids, e)
	}
	if next, _, e := s.ClaimBatch(1); e != nil || next != 0 {
		t.Fatal("concurrent undo")
	}
	if claimed, e := s.ClaimDraft(id, 1); e != nil || claimed != nil {
		t.Fatal("write while undo")
	}
	if e = s.RecordUndo(batch, 2, "a"); e == nil {
		t.Fatal("cross owner undo")
	}
	if e = s.RecordUndo(batch, 1, "a"); e != nil {
		t.Fatal(e)
	}
	if e = s.ReleaseBatch(batch, 1); e != nil {
		t.Fatal(e)
	}
	if next, _, e := s.ClaimBatch(1); e != nil || next != 0 {
		t.Fatal("empty batch retained")
	}
	d, e = s.Draft(id, 1)
	if e != nil || d.Items[0].Status != "undone" {
		t.Fatal(d, e)
	}
}
func TestStoreFailedUndoKeepsIDsAndExpiredClaim(t *testing.T) {
	s := openTestStore(t)
	d := readyDraft()
	id, e := s.SaveDraft(1, d)
	if e != nil {
		t.Fatal(e)
	}
	if e = s.RecordWrite(id, 1, d, "a"); e != nil {
		t.Fatal(e)
	}
	if e = s.RecordWrite(id, 1, d, "b"); e != nil {
		t.Fatal(e)
	}
	batch, _, e := s.ClaimBatch(1)
	if e != nil {
		t.Fatal(e)
	}
	if e = s.RecordUndo(batch, 1, "a"); e != nil {
		t.Fatal(e)
	}
	s.ReleaseBatch(batch, 1)
	_, ids, e := s.ClaimBatch(1)
	if e != nil || len(ids) != 1 || ids[0] != "b" {
		t.Fatal(ids, e)
	}
	s.ReleaseBatch(batch, 1)
	if _, e = s.db.Exec(`UPDATE drafts SET writing=1,writing_since=? WHERE draft_id=?`, time.Now().Unix()-claimTTL-1, id); e != nil {
		t.Fatal(e)
	}
	if d, e := s.ClaimDraft(id, 1); e != nil || d == nil {
		t.Fatal("stale lease", e)
	}
}
func TestStoreUsersAndBindings(t *testing.T) {
	s := openTestStore(t)
	u, e := s.EnsureUser(1)
	if e != nil || u.Linked() || u.Allowed {
		t.Fatal(u, e)
	}
	for _, e := range []error{s.Allow(1), s.SaveLink(1, "synthetic-token", "synthetic-secret"), s.SetTZ(1, "Europe/Berlin"), s.BindBarcode(1, "4006381333931", "42")} {
		if e != nil {
			t.Fatal(e)
		}
	}
	u, e = s.User(1)
	if e != nil || !u.Linked() || !u.Allowed || u.TZ != "Europe/Berlin" {
		t.Fatal(u, e)
	}
	if e = s.InvalidateLink(1); e != nil {
		t.Fatal(e)
	}
	u, e = s.User(1)
	if e != nil || u.Linked() || u.TZ != "Europe/Berlin" {
		t.Fatal(u, e)
	}
	if id, e := s.BoundFood(2, "4006381333931"); e != nil || id != "" {
		t.Fatal("cross owner binding")
	}
	if id, e := s.BoundFood(1, "4006381333931"); e != nil || id != "42" {
		t.Fatal(id, e)
	}
}

func TestRealPythonState(t *testing.T) {
	b, e := os.ReadFile("../../testdata/python-state.sqlite")
	if e != nil {
		t.Fatal(e)
	}
	path := filepath.Join(t.TempDir(), "state.sqlite3")
	if e = os.WriteFile(path, b, 0600); e != nil {
		t.Fatal(e)
	}
	s, e := OpenStore(path)
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	u, e := s.User(42)
	if e != nil || !u.Linked() || !u.Allowed || u.TZ != "Europe/Berlin" || u.Token != "synthetic-token" {
		t.Fatal(u, e)
	}
	if id, e := s.BoundFood(42, "0036000291452"); e != nil || id != "77" {
		t.Fatal(id, e)
	}
	id, d, e := s.LastDraft(42)
	if e != nil || d.CardMessageID != 123 || d.Items[0].Status != "written" || d.Items[1].Status != "unknown" {
		t.Fatal(d, e)
	}
	f := &fakeFoods{}
	r, e := WriteDraft(context.Background(), f, d, u, func() error { return s.UpdateDraft(id, 42, d) }, func(entry string) error { return s.RecordWrite(id, 42, d, entry) })
	if e != nil || len(f.calls) != 0 || len(r.Failed) != 1 {
		t.Fatal("migration repeated old write", r, e)
	}
	batch, ids, e := s.ClaimBatch(42)
	if e != nil || len(ids) != 1 || ids[0] != "old-entry" {
		t.Fatal(ids, e)
	}
	if e = s.RecordUndo(batch, 42, "old-entry"); e != nil {
		t.Fatal(e)
	}
	d, e = s.Draft(id, 42)
	if e != nil || d.Items[0].Status != "undone" {
		t.Fatal(d, e)
	}
}
