package fsbot

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
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
