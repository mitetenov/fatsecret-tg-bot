package fsbot

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync/atomic"
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
		{"empty-null", Object{"food_entries": nil}, 0, false},
		{"absent", Object{}, 0, true},
		{"wrong-envelope", Object{"food_entries": []Object{valid}}, 0, true},
		{"empty-envelope-array", Object{"food_entries": []Object{}}, 0, true},
		{"nonempty-envelope-string", Object{"food_entries": "unavailable"}, 0, true},
		{"boolean-envelope", Object{"food_entries": false}, 0, true},
		{"numeric-envelope", Object{"food_entries": 0}, 0, true},
		{"null-entry-list", Object{"food_entries": Object{"food_entry": nil}}, 0, true},
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

func TestWriteDraftWithNullDiary(t *testing.T) {
	for _, confirmed := range []bool{true, false} {
		t.Run(fmt.Sprintf("confirmed=%t", confirmed), func(t *testing.T) {
			d := readyDraft()
			d.Items = d.Items[:1]
			entry := newEntryWrite(d, d.Items[0], nil).Expected
			entry.ID = "101"
			var reads, creates atomic.Int32
			var saved, visible atomic.Bool
			server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				if e := r.ParseForm(); e != nil {
					t.Error(e)
				}
				switch r.Form.Get("method") {
				case "food_entries.get.v2":
					reads.Add(1)
					if visible.Load() {
						json.NewEncoder(w).Encode(Object{"food_entries": Object{"food_entry": []Object{diaryObject(entry)}}})
					} else {
						io.WriteString(w, `{"food_entries":null}`)
					}
				case "food_entry.create":
					creates.Add(1)
					if r.Method != http.MethodPost || !saved.Load() {
						t.Error("write sent before persisting the empty diary snapshot")
					}
					if confirmed {
						io.WriteString(w, `{"food_entries":{"food_entry":{"food_entry_id":"101"}}}`)
					} else {
						io.WriteString(w, `{}`)
					}
				default:
					t.Error("unexpected FatSecret method")
					w.WriteHeader(http.StatusBadRequest)
				}
			})
			f := NewFatSecret("key", "secret")
			f.API = server.URL
			var persisted []byte
			persist := func() error {
				a := d.Items[0].EntryWrite
				if a == nil || a.BeforeIDs == nil || len(a.BeforeIDs) != 0 {
					t.Fatal("empty diary snapshot must be retained", a)
				}
				var e error
				persisted, e = json.Marshal(d)
				saved.Store(e == nil)
				return e
			}
			recorded := []string{}
			write := func() WriteReport {
				t.Helper()
				r, e := WriteDraft(context.Background(), f, d, &User{}, persist, func(id string) error {
					recorded = append(recorded, id)
					return nil
				})
				if e != nil {
					t.Fatal(e)
				}
				return r
			}
			r := write()
			if !confirmed {
				if len(r.Failed) != 1 || len(r.Written) != 0 || len(recorded) != 0 || d.Items[0].Status != "unknown" || reads.Load() != 2 {
					t.Fatal("unconfirmed write must remain blocked", r, d.Items[0])
				}
				// Restore the saved empty snapshot, as after a restart.
				d = &Draft{}
				if e := json.Unmarshal(persisted, d); e != nil {
					t.Fatal(e)
				}
				r = write()
				if len(r.Failed) != 1 || creates.Load() != 1 || reads.Load() != 3 || len(recorded) != 0 {
					t.Fatal("null diary must not replay or confirm an uncertain write", r)
				}
				visible.Store(true)
				r = write()
			}
			if len(r.Failed) != 0 || len(r.Written) != 1 || d.Items[0].Status != "written" || d.Items[0].EntryID != "101" || len(recorded) != 1 || recorded[0] != "101" || creates.Load() != 1 {
				t.Fatal("entry was not confirmed exactly once", r, d.Items[0], recorded)
			}
			before := reads.Load()
			r = write()
			if len(r.Written) != 0 || len(r.Failed) != 0 || creates.Load() != 1 || reads.Load() != before || len(recorded) != 1 {
				t.Fatal("confirmed entry was replayed", r)
			}
		})
	}
}

func TestInvalidDiaryResponseNeverSendsWrite(t *testing.T) {
	for _, tc := range []struct {
		name   string
		status int
		body   string
	}{
		{"absent-envelope", 200, `{}`},
		{"array-envelope", 200, `{"food_entries":[]}`},
		{"null-entry-list", 200, `{"food_entries":{"food_entry":null}}`},
		{"null-response", 200, `null`},
		{"invalid-json", 200, `{"food_entries":`},
		{"api-error", 200, `{"error":{"code":"1","message":"unavailable"},"food_entries":null}`},
		{"http-error", 500, `{"food_entries":null}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var creates atomic.Int32
			server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				if r.Method == http.MethodPost {
					creates.Add(1)
				}
				w.WriteHeader(tc.status)
				io.WriteString(w, tc.body)
			})
			f := NewFatSecret("key", "secret")
			f.API = server.URL
			d := readyDraft()
			r, e := WriteDraft(context.Background(), f, d, &User{}, func() error { return nil }, func(string) error {
				t.Error("recorded a write after a failed diary check")
				return nil
			})
			if e != nil || len(r.Failed) != 1 || len(r.Written) != 0 || creates.Load() != 0 || d.Items[0].Status != "pending" || d.Items[0].EntryWrite != nil {
				t.Fatal("invalid diary response allowed a write", r, e, d.Items[0])
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

// Restore the persisted attempt through a fresh SQLite connection.

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

// Another failed check must only read. The matching entry appears later.

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
