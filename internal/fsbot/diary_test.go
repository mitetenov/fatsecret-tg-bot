package fsbot

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
)

func diaryObject(e DiaryEntry) Object {
	return Object{"food_entry_id": e.ID, "food_id": e.FoodID, "serving_id": e.ServingID,
		"date_int": e.Date, "meal": e.Meal, "food_entry_name": e.Name, "number_of_units": e.Units}
}

func TestDiaryResponseContracts(t *testing.T) {
	d := readyDraft()
	entry := newEntryWrite(d, d.Items[0], nil).Expected
	entry.ID, entry.Meal = "101", "Lunch"
	valid := diaryObject(entry)
	for _, tc := range []struct {
		name string
		body Object
		size int
		bad  bool
	}{
		{"array", Object{"food_entries": Object{"food_entry": []Object{valid}}}, 1, false},
		{"singleton", Object{"food_entries": Object{"food_entry": valid}}, 1, false},
		{"empty-array", Object{"food_entries": Object{"food_entry": []Object{}}}, 0, false},
		{"empty-object", Object{"food_entries": Object{}}, 0, false},
		{"empty-string", Object{"food_entries": ""}, 0, false},
		{"absent", Object{}, 0, true},
		{"null", Object{"food_entries": nil}, 0, true},
		{"wrong-envelope", Object{"food_entries": []Object{valid}}, 0, true},
		{"wrong-list", Object{"food_entries": Object{"food_entry": ""}}, 0, true},
		{"missing-id", Object{"food_entries": Object{"food_entry": Object{"food_id": "a"}}}, 0, true},
		{"invalid-id", Object{"food_entries": Object{"food_entry": Object{"food_entry_id": Object{"value": "101"}}}}, 0, true},
		{"duplicate-id", Object{"food_entries": Object{"food_entry": []Object{valid, valid}}}, 0, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				p := r.URL.Query()
				if r.Method != "GET" || p.Get("method") != "food_entries.get.v2" || p.Get("date") != "20724" || p.Get("oauth_token") != "token" {
					t.Error("incorrect diary request")
				}
				json.NewEncoder(w).Encode(tc.body)
			})
			f := NewFatSecret("key", "secret")
			f.API = server.URL
			entries, e := f.Entries(context.Background(), &User{Token: "token"}, d.Day)
			if (e != nil) != tc.bad || len(entries) != tc.size {
				t.Fatal(entries, e)
			}
			if len(entries) == 1 && entries[0].Meal != "lunch" {
				t.Fatal("meal casing not normalized")
			}
		})
	}
}

func TestReconciliationRequiresUniqueNewExactMatch(t *testing.T) {
	d := readyDraft()
	i := d.Items[0]
	i.Units = 1.234567
	i.Title = strings.Repeat("я", 65)
	want := newEntryWrite(d, i, nil).Expected
	if want.Units != 1.2346 || len([]rune(want.Name)) != 60 {
		t.Fatal("fingerprint must match transmitted values", want)
	}
	want.ID = "old"
	i.EntryWrite = newEntryWrite(d, i, []DiaryEntry{want})
	i.Status = "unknown"
	for _, field := range []string{"match", "food", "serving", "date", "meal", "name", "units", "absent", "ambiguous", "old-only", "read-error", "legacy"} {
		t.Run(field, func(t *testing.T) {
			copy := *i
			candidate := want
			candidate.ID = "new"
			f := &fakeFoods{diary: []DiaryEntry{want}}
			switch field {
			case "food":
				candidate.FoodID = "other"
			case "serving":
				candidate.ServingID = "other"
			case "date":
				candidate.Date = "20725"
			case "meal":
				candidate.Meal = "dinner"
			case "name":
				candidate.Name = "other"
			case "units":
				candidate.Units += .001
			case "ambiguous":
				another := candidate
				another.ID = "another"
				f.diary = append(f.diary, another)
			case "read-error":
				f.diaryError = errors.New("unavailable")
			case "legacy":
				copy.EntryWrite = nil
			}
			if field != "absent" && field != "old-only" {
				f.diary = append(f.diary, candidate)
			}
			id, e := reconcileEntry(context.Background(), f, &User{}, &copy)
			if field == "match" {
				if e != nil || id != "new" {
					t.Fatal(id, e)
				}
			} else if e == nil || id != "" {
				t.Fatal("unsafe reconciliation", id, e)
			}
			if len(f.calls) != 0 {
				t.Fatal("reconciliation wrote an entry")
			}
		})
	}
}

func TestWriteSnapshotSurvivesRestartAndRecordFailure(t *testing.T) {
	for _, crashState := range []string{"unknown", "writing"} {
		t.Run(crashState, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "state.sqlite3")
			s, e := OpenStore(path)
			if e != nil {
				t.Fatal(e)
			}
			defer s.Close()
			u := &User{ID: 1}
			d := readyDraft()
			d.Items = d.Items[:1]
			id, e := s.SaveDraft(u.ID, d)
			if e != nil {
				t.Fatal(e)
			}
			f := &fakeFoods{}
			var created DiaryEntry
			f.create = func(_ *Draft, i *Item) (string, error) {
				stored, e := s.Draft(id, u.ID)
				if e != nil || stored.Items[0].Status != "writing" || stored.Items[0].EntryWrite == nil || stored.Items[0].EntryWrite.BeforeIDs == nil {
					t.Fatal("snapshot not durable before request", stored, e)
				}
				created = i.EntryWrite.Expected
				created.ID = "101"
				return "", unknown("lost response")
			}
			persist := func() error { return s.UpdateDraft(id, u.ID, d) }
			r, e := WriteDraft(context.Background(), f, d, u, persist, func(string) error { t.Fatal("unexpected record"); return nil })
			if e != nil || len(r.Failed) != 1 || d.Items[0].Status != "unknown" || len(f.calls) != 1 {
				t.Fatal(r, e)
			}
			d.Items[0].Status = crashState
			if e = persist(); e != nil {
				t.Fatal(e)
			}
			// Restore the persisted attempt through a fresh SQLite connection.
			reopened, e := OpenStore(path)
			if e != nil {
				t.Fatal(e)
			}
			defer reopened.Close()
			d, e = reopened.Draft(id, u.ID)
			if e != nil {
				t.Fatal(e)
			}
			f.diary = []DiaryEntry{created}
			persist = func() error { return reopened.UpdateDraft(id, u.ID, d) }
			_, e = WriteDraft(context.Background(), f, d, u, persist, func(string) error { return errors.New("disk full") })
			if e == nil {
				t.Fatal("record failure ignored")
			}
			d, e = reopened.Draft(id, u.ID)
			if e != nil || d.Items[0].Status != crashState {
				t.Fatal("failed record lost retry state", d, e)
			}
			r, e = WriteDraft(context.Background(), f, d, u, persist, func(entry string) error { return reopened.RecordWrite(id, u.ID, d, entry) })
			if e != nil || !reflect.DeepEqual(r.EntryIDs, []string{"101"}) || len(f.calls) != 1 || d.Items[0].EntryWrite != nil {
				t.Fatal(r, e, f.calls)
			}
			_, ids, e := reopened.ClaimBatch(u.ID)
			if e != nil || !reflect.DeepEqual(ids, []string{"101"}) {
				t.Fatal("recovered ID not available to undo", ids, e)
			}
		})
	}
}

func TestPreflightFailureNeverSendsWrite(t *testing.T) {
	for _, failure := range []error{unknown("timeout"), &FSError{9, "revoked"}} {
		t.Run(fmt.Sprint(failure), func(t *testing.T) {
			d := readyDraft()
			f := &fakeFoods{diaryError: failure}
			r, e := WriteDraft(context.Background(), f, d, &User{}, func() error { return nil }, func(string) error { t.Fatal("recorded without write"); return nil })
			if e != nil || len(f.calls) != 0 || len(r.Failed) != 1 || d.Items[0].Status != "pending" || d.Items[0].EntryWrite != nil {
				t.Fatal(r, e)
			}
			fe, revoked := failure.(*FSError)
			if r.TokenInvalid != (revoked && fe.TokenInvalid()) {
				t.Fatal("token revocation lost")
			}
		})
	}
}

func TestCallbackReconcilesSavedEntryWithoutDuplicates(t *testing.T) {
	for _, mode := range []string{"missing-id", "lost-response", "delayed", "full-diary", "invalid-json"} {
		t.Run(mode, func(t *testing.T) {
			h := newHarness(t)
			h.entryMode = mode
			if mode == "delayed" {
				h.entryMode, h.hideNewEntries = "missing-id", true
			}
			d := readyDraft()
			d.Items = d.Items[:1]
			old := newEntryWrite(d, d.Items[0], nil).Expected
			old.ID = "100"
			if mode != "delayed" {
				h.diary = []Object{diaryObject(old)}
			}
			id, e := h.app.Store.SaveDraft(42, d)
			if e != nil {
				t.Fatal(e)
			}
			h.send(t, h.callback(id, "w", ""))
			if mode == "delayed" {
				stored, e := h.app.Store.Draft(id, 42)
				if e != nil || stored == nil || stored.Items[0].Status != "unknown" {
					t.Fatal(stored, e)
				}
				if DraftKeyboard(id, stored).Rows[0][0].Text != "🔎 Проверить запись" {
					t.Fatal("check-only action not shown")
				}
				// Another failed check must only read. The matching entry appears later.
				h.send(t, h.callback(id, "w", ""))
				h.hideNewEntries = false
				h.send(t, h.callback(id, "w", ""))
			}
			h.send(t, h.callback(id, "w", ""))
			if h.writes != 1 {
				t.Fatal("duplicate external create", h.writes)
			}
			if stored, e := h.app.Store.Draft(id, 42); e != nil || stored != nil {
				t.Fatal("confirmed draft not finished", stored, e)
			}
			success := false
			for _, call := range h.calls {
				if call["method"] == "editMessageText" && strings.Contains(str(call["text"]), "✅ A") {
					success = true
				}
			}
			if !success {
				t.Fatal("Telegram did not confirm the recovered entry")
			}
			h.send(t, h.message("/undo"))
			h.send(t, h.message("/undo"))
			if h.deletes != 1 {
				t.Fatal("undo must delete recovered ID exactly once", h.deletes)
			}
		})
	}
}

func TestUncertainAttemptCheckedBeforeAnyNewWrites(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		t.Run(fmt.Sprint(legacy), func(t *testing.T) {
			d := readyDraft()
			// The safe-to-retry item precedes an uncertain identical one. Writing it
			// first could make it look like the result of the second item's request.
			copy := *d.Items[0]
			d.Items[1] = &copy
			d.Items[1].Status = "unknown"
			if !legacy {
				d.Items[1].EntryWrite = newEntryWrite(d, d.Items[1], nil)
			}
			f := &fakeFoods{}
			for range 2 {
				r, e := WriteDraft(context.Background(), f, d, &User{}, func() error { return nil }, func(string) error { return nil })
				if e != nil || len(r.Failed) != 1 || len(f.calls) != 0 || d.Items[0].Status != "pending" {
					t.Fatal("new write sent before resolving uncertain result", r, e, f.calls)
				}
			}
		})
	}
}

func TestAmbiguousResultNeverRetriesOrRecordsAnID(t *testing.T) {
	h := newHarness(t)
	h.entryMode = "ambiguous"
	d := readyDraft()
	d.Items = d.Items[:1]
	id, e := h.app.Store.SaveDraft(42, d)
	if e != nil {
		t.Fatal(e)
	}
	for range 3 {
		h.send(t, h.callback(id, "w", ""))
	}
	stored, e := h.app.Store.Draft(id, 42)
	if e != nil || stored == nil || stored.Items[0].Status != "unknown" || stored.Items[0].EntryID != "" || h.writes != 1 {
		t.Fatal("ambiguous result was retried or recorded", stored, e, h.writes)
	}
	h.send(t, h.message("/undo"))
	if h.deletes != 0 {
		t.Fatal("undo selected an ambiguous entry")
	}
}

func TestConcurrentCallbacksReconcileOneExternalCreate(t *testing.T) {
	h := newHarness(t)
	h.entryMode = "lost-response"
	d := readyDraft()
	d.Items = d.Items[:1]
	id, e := h.app.Store.SaveDraft(42, d)
	if e != nil {
		t.Fatal(e)
	}
	errors := make(chan error, 2)
	var wg sync.WaitGroup
	for range 2 {
		wg.Go(func() { errors <- h.app.Handle(context.Background(), h.callback(id, "w", "")) })
	}
	wg.Wait()
	close(errors)
	for e := range errors {
		if e != nil {
			t.Fatal(e)
		}
	}
	if h.writes != 1 {
		t.Fatal("concurrent callbacks repeated create", h.writes)
	}
	if stored, e := h.app.Store.Draft(id, 42); e != nil || stored != nil {
		t.Fatal("concurrent confirmation lost", stored, e)
	}
}

func TestCreateEntryDoesNotFollowRedirect(t *testing.T) {
	calls := 0
	server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.Header().Set("Location", "/redirected")
		w.WriteHeader(http.StatusTemporaryRedirect)
		w.Write([]byte(`{}`))
	})
	f := NewFatSecret("key", "secret")
	f.API = server.URL
	d := readyDraft()
	_, e := f.CreateEntry(context.Background(), &User{}, d, d.Items[0])
	if e == nil || calls != 1 {
		t.Fatal("create replayed through redirect", calls, e)
	}
}
