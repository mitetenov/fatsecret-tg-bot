package fsbot

import (
	"context"
	"errors"
	"fmt"
	"testing"
)

type fakeFoods struct {
	food         Object
	found        []Candidate
	calls        []string
	failure      map[string]error
	queries      []string
	autocomplete []string
	diary        []DiaryEntry
	diaryError   error
	reads        int
	create       func(*Draft, *Item) (string, error)
}

func (f *fakeFoods) Search(_ context.Context, q string) ([]Candidate, error) {
	f.queries = append(f.queries, q)
	return f.found, nil
}
func (f *fakeFoods) Autocomplete(context.Context, string) ([]string, error) {
	return f.autocomplete, nil
}
func (f *fakeFoods) Food(context.Context, string) (Object, error) {
	if f.food == nil {
		return nil, fmt.Errorf("unexpected food request")
	}
	return f.food, nil
}
func (f *fakeFoods) Entries(context.Context, *User, string) ([]DiaryEntry, error) {
	f.reads++
	return append([]DiaryEntry(nil), f.diary...), f.diaryError
}
func (f *fakeFoods) CreateEntry(_ context.Context, _ *User, d *Draft, i *Item) (string, error) {
	f.calls = append(f.calls, i.FoodID)
	if f.create != nil {
		return f.create(d, i)
	}
	if e := f.failure[i.FoodID]; e != nil {
		return "", e
	}
	return "entry-" + i.FoodID, nil
}
func readyDraft() *Draft {
	return &Draft{Day: "2026-09-28", Meal: "lunch", Items: []*Item{{Name: "A", FoodID: "a", ServingID: "1", Units: 1, Status: "pending"}, {Name: "B", FoodID: "b", ServingID: "2", Units: 1, Status: "pending"}}}
}

func TestWriteKnownFailureRetryAndToken(t *testing.T) {
	for _, code := range []int{9, 12} {
		t.Run(fmt.Sprint(code), func(t *testing.T) {
			d := readyDraft()
			f := &fakeFoods{failure: map[string]error{"a": &FSError{code, "denied"}}}
			writes := []string{}
			r, e := WriteDraft(context.Background(), f, d, &User{}, func() error { return nil }, func(id string) error { writes = append(writes, id); return nil })
			if e != nil || d.Items[0].Status != "failed" || r.TokenInvalid != (code == 9) {
				t.Fatal(r, e)
			}
			if code == 9 && len(f.calls) != 1 {
				t.Fatal("invalid token must stop batch")
			}
			delete(f.failure, "a")
			r, e = WriteDraft(context.Background(), f, d, &User{}, func() error { return nil }, func(id string) error { writes = append(writes, id); return nil })
			if e != nil || len(writes) != 2 {
				t.Fatal(r, e, writes)
			}
		})
	}
}
func TestPersistBeforeNetworkAndCrashSafety(t *testing.T) {
	d := readyDraft()
	f := &fakeFoods{}
	_, e := WriteDraft(context.Background(), f, d, &User{}, func() error { return errors.New("disk full") }, func(string) error { return nil })
	if e == nil || len(f.calls) != 0 {
		t.Fatal("write before persistence")
	}
	for _, status := range []string{"writing", "unknown", "creating", "create_unknown", "written", "undone"} {
		d = readyDraft()
		d.Items = d.Items[:1]
		d.Items[0].Status = status
		_, e = WriteDraft(context.Background(), f, d, &User{}, func() error { return nil }, func(string) error { return nil })
		if e != nil || len(f.calls) != 0 {
			t.Fatal("protected state repeated", status)
		}
	}
}
func TestChooseManualPortion(t *testing.T) {
	f := &fakeFoods{food: Object{"servings": Object{"serving": Object{"serving_id": "1", "serving_description": "1 cup", "calories": "100"}}}}
	i := &Item{Name: "еда", Amount: 200, Unit: "g", Candidates: []Candidate{{FoodID: "42", Title: "Food"}}}
	if e := ApplyCandidate(context.Background(), f, i, 0); e != nil {
		t.Fatal(e)
	}
	if !i.NeedsPortion || i.ServingID != "" {
		t.Fatal(i)
	}
	i.ManualServingID = "1"
	i.Amount = 1.5
	i.Unit = "piece"
	if e := ApplyCandidate(context.Background(), f, i, 0); e != nil || i.NeedsPortion || i.Kcal != 150 || i.Units != 1.5 {
		t.Fatal(i, e)
	}
}
func TestRecentRankingAndInlineDetails(t *testing.T) {
	food := Object{"servings": Object{"serving": Object{"serving_id": "1", "metric_serving_amount": 100, "metric_serving_unit": "g", "number_of_units": 100, "calories": 120}}}
	f := &fakeFoods{found: []Candidate{{FoodID: "a", Title: "A", Food: food}, {FoodID: "b", Title: "B", Food: food}}}
	r := Recognition{Kind: "text", Items: []RecognizedItem{{Query: "oats", Name: "овсянка", Amount: 150, Unit: "g", Confidence: .8}, {Query: "apple", Name: "яблоко", Amount: 100, Unit: "g", Meal: "dinner", Confidence: .8}}}
	d, e := BuildDraft(context.Background(), f, r, "UTC", []Candidate{{FoodID: "b"}}, "")
	if e != nil || d.Items[0].FoodID != "b" || d.Items[0].Kcal != 180 || d.Meal != "dinner" {
		t.Fatal(d, e)
	}
}

func TestOldCalorieOnlyLabel(t *testing.T) {
	i := &Item{Name: "Old label", Amount: 100, Unit: "g", LabelKcal: 100, Confidence: .8, Candidates: []Candidate{{FoodID: "42", Title: "Different food", Food: Object{"servings": Object{"serving": Object{"serving_id": "1", "metric_serving_amount": 100, "metric_serving_unit": "g", "calories": 500, "number_of_units": 100}}}}}}
	if e := ApplyCandidate(context.Background(), &fakeFoods{}, i, 0); e != nil || i.Mismatch == nil {
		t.Fatal(i, e)
	}
}
