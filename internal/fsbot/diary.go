package fsbot

import (
	"context"
	"fmt"
	"math"
	"strconv"
	"strings"
)

// Only the fields needed to identify a write are retained; nutrition is not stored.
type DiaryEntry struct {
	ID        string  `json:"id"`
	FoodID    string  `json:"food_id"`
	ServingID string  `json:"serving_id"`
	Date      string  `json:"date"`
	Meal      string  `json:"meal"`
	Name      string  `json:"name"`
	Units     float64 `json:"units"`
}

// Persisted before the create request, including when the diary is empty.
type EntryWrite struct {
	Day       string     `json:"day"`
	BeforeIDs []string   `json:"before_ids"`
	Expected  DiaryEntry `json:"expected"`
}

func newEntryWrite(d *Draft, i *Item, before []DiaryEntry) *EntryWrite {
	date, _ := FatSecretDate(d.Day)
	units, _ := strconv.ParseFloat(fmt.Sprintf("%.4f", i.Units), 64)
	a := &EntryWrite{Day: d.Day, BeforeIDs: []string{}, Expected: DiaryEntry{
		FoodID: i.FoodID, ServingID: i.ServingID, Date: strconv.FormatInt(date, 10),
		Meal: d.Meal, Name: short(first(i.Title, i.Name), 60), Units: units,
	}}
	for _, entry := range before {
		a.BeforeIDs = append(a.BeforeIDs, entry.ID)
	}
	return a
}

func (f *FatSecret) Entries(ctx context.Context, u *User, day string) ([]DiaryEntry, error) {
	date, e := FatSecretDate(day)
	if e != nil {
		return nil, e
	}
	p, e := f.Call(ctx, "food_entries.get.v2", u.Token, u.Secret, Object{"date": date})
	if e != nil {
		return nil, e
	}
	// An absent or malformed envelope is not evidence of an empty diary.
	raw, ok := p["food_entries"]
	if !ok {
		return nil, unknown("FatSecret не вернул дневник")
	}
	// FatSecret returns an explicit null envelope when the diary is empty.
	if raw == nil || raw == "" {
		return []DiaryEntry{}, nil
	}
	envelope, ok := raw.(map[string]any)
	if !ok {
		return nil, unknown("Неожиданный формат дневника FatSecret")
	}
	raw, ok = envelope["food_entry"]
	if !ok && len(envelope) == 0 {
		return []DiaryEntry{}, nil
	}
	entries := list(raw)
	if entries == nil {
		return nil, unknown("Неожиданный формат записей FatSecret")
	}
	out := []DiaryEntry{}
	seen := map[string]bool{}
	for _, raw := range entries {
		v := obj(raw)
		units, valid := number(v["number_of_units"])
		entry := DiaryEntry{ID: str(v["food_entry_id"]), FoodID: str(v["food_id"]),
			ServingID: str(v["serving_id"]), Date: str(v["date_int"]),
			Meal: strings.ToLower(str(v["meal"])), Name: str(v["food_entry_name"]), Units: units}
		id, idError := strconv.ParseUint(entry.ID, 10, 64)
		if idError != nil || id == 0 || seen[entry.ID] || entry.FoodID == "" ||
			entry.ServingID == "" || entry.Date == "" || MealRU[entry.Meal] == "" || !valid || units <= 0 {
			return nil, unknown("Неполные записи дневника FatSecret")
		}
		seen[entry.ID] = true
		out = append(out, entry)
	}
	return out, nil
}

func reconcileEntry(ctx context.Context, fs Foods, u *User, i *Item) (string, error) {
	a := i.EntryWrite
	if a == nil || a.BeforeIDs == nil {
		return "", unknown("Нет снимка дневника до записи")
	}
	entries, e := fs.Entries(ctx, u, a.Day)
	if e != nil {
		return "", e
	}
	before := map[string]bool{}
	for _, id := range a.BeforeIDs {
		before[id] = true
	}
	id := ""
	want := a.Expected
	for _, got := range entries {
		if before[got.ID] || got.FoodID != want.FoodID || got.ServingID != want.ServingID ||
			got.Date != want.Date || got.Meal != want.Meal || got.Name != want.Name ||
			math.Abs(got.Units-want.Units) > 1e-7 {
			continue
		}
		if id != "" {
			return "", unknown("В дневнике несколько новых совпадающих записей")
		}
		id = got.ID
	}
	if id == "" {
		return "", unknown("Новая совпадающая запись в дневнике пока не найдена")
	}
	return id, nil
}
