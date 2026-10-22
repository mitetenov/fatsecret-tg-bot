package fsbot

import (
	"context"
	"errors"
	"fmt"
	"html"
	"math"
	"sort"
	"strings"
	"time"
)

type Foods interface {
	Search(context.Context, string) ([]Candidate, error)
	Autocomplete(context.Context, string) ([]string, error)
	Food(context.Context, string) (Object, error)
	CreateEntry(context.Context, *User, *Draft, *Item) (string, error)
	Entries(context.Context, *User, string) ([]DiaryEntry, error)
}

func ApplyCandidate(ctx context.Context, fs Foods, i *Item, chosen int) error {
	if len(i.Candidates) == 0 {
		return nil
	}
	chosen = max(0, min(chosen, len(i.Candidates)-1))
	c := &i.Candidates[chosen]
	food := c.Food
	if len(food) == 0 {
		var e error
		food, e = fs.Food(ctx, c.FoodID)
		if e != nil {
			i.FoodID = ""
			i.Error = e.Error()
			var fe *FSError
			if errors.As(e, &fe) {
				return nil
			}
			return e
		}
		c.Food = food
	}
	servings := ParseServings(food)
	var portion *Portion
	if i.ManualServingID != "" {
		for _, s := range servings {
			if s.ID == i.ManualServingID {
				portion = &Portion{s, i.Amount}
				break
			}
		}
	} else {
		portion = DefaultPortion(servings, i.Amount, i.Unit)
	}
	i.Chosen = chosen
	i.Title = c.Title
	i.FoodID = c.FoodID
	if portion == nil {
		i.ServingID = ""
		i.Units = 0
		i.NeedsPortion = len(servings) > 0
		i.Available = nil
		for _, s := range servings[:min(15, len(servings))] {
			i.Available = append(i.Available, ServingChoice{s.ID, s.Description})
		}
		i.Error = "выбери порцию и её количество"
		if len(servings) == 0 {
			i.FoodID = ""
			i.Error = "у продукта нет ни одной порции"
		}
		return nil
	}
	i.Mismatch = nil
	if i.LabelKcal > 0 {
		label := Nutrition{Kcal: i.LabelKcal}
		if i.LabelMacros != nil {
			label = *i.LabelMacros
		}
		ok, gap := matchesLabel(label, servings, first(i.LabelBasis, "g"), i.LabelMacros != nil)
		if !ok {
			i.Mismatch = gap
		}
	}
	i.ServingID = portion.Serving.ID
	i.Units = portion.APIUnits()
	i.Portion = portion.Describe()
	i.Kcal = round(portion.Serving.Calories*portion.Multiplier, 1)
	i.Protein = round(portion.Serving.Protein*portion.Multiplier, 1)
	i.Fat = round(portion.Serving.Fat*portion.Multiplier, 1)
	i.Carbs = round(portion.Serving.Carbs*portion.Multiplier, 1)
	i.Error = ""
	i.NeedsPortion = false
	i.Available = nil
	if i.Mismatch != nil {
		i.Confidence = math.Min(i.Confidence, .35)
	} else if i.LabelKcal > 0 {
		i.Confidence = math.Max(i.Confidence, .85)
	}
	return nil
}
func PickBest(ctx context.Context, fs Foods, i *Item) error {
	if i.LabelKcal > 0 {
		for p := range i.Candidates {
			if e := ApplyCandidate(ctx, fs, i, p); e != nil {
				return e
			}
			if i.FoodID != "" && i.Mismatch == nil {
				return nil
			}
		}
	}
	return ApplyCandidate(ctx, fs, i, 0)
}
func attachLabel(i *Item, n *Nutrition, brand string) {
	if n == nil {
		return
	}
	i.LabelKcal = n.Kcal
	i.LabelMacros = n
	i.LabelBasis = n.BasisUnit
	i.Creatable = &FoodSpec{*n, i.Name, first(brand, "fsbot")}
}
func BuildDraft(ctx context.Context, fs Foods, r Recognition, tz string, recent []Candidate, barcode string) (*Draft, error) {
	meal, hint := "", ""
	for _, i := range r.Items {
		meal = first(meal, i.Meal)
		hint = first(hint, i.DateHint)
	}
	day, m, e := Resolve(tz, time.Now(), meal, hint)
	if e != nil {
		return nil, e
	}
	d := &Draft{Day: day, Meal: m, Kind: r.Kind, Barcode: first(barcode, r.Barcode)}
	recentIDs := map[string]bool{}
	for _, c := range recent {
		recentIDs[c.FoodID] = true
	}
	for _, ri := range r.Items {
		i := &Item{Name: ri.Name, Query: ri.Query, Amount: ri.Amount, Unit: ri.Unit, Status: "pending", Confidence: ri.Confidence}
		found, e := fs.Search(ctx, ri.Query)
		if e == nil && len(found) == 0 {
			var suggestions []string
			suggestions, e = fs.Autocomplete(ctx, ri.Query)
			if e == nil {
				for _, q := range suggestions[:min(2, len(suggestions))] {
					found, e = fs.Search(ctx, q)
					if e != nil || len(found) > 0 {
						break
					}
				}
			}
		}
		if e != nil {
			var fe *FSError
			if !errors.As(e, &fe) {
				return nil, e
			}
			i.Error = e.Error()
			d.Items = append(d.Items, i)
			continue
		}
		sort.SliceStable(found, func(a, b int) bool { return recentIDs[found[a].FoodID] && !recentIDs[found[b].FoodID] })
		i.Candidates = found
		attachLabel(i, ri.Nutrition, ri.Brand)
		if e = PickBest(ctx, fs, i); e != nil {
			return nil, e
		}
		d.Items = append(d.Items, i)
	}
	d.Refresh()
	return d, nil
}
func DraftFromFood(ctx context.Context, fs Foods, id, tz string) (*Draft, error) {
	food, e := fs.Food(ctx, id)
	if e != nil {
		return nil, e
	}
	title := strings.TrimSpace(str(food["brand_name"]) + " " + str(food["food_name"]))
	day, meal, e := Resolve(tz, time.Now(), "", "")
	if e != nil {
		return nil, e
	}
	i := &Item{Name: title, Query: title, Amount: 100, Unit: "g", Status: "pending", Confidence: 1, Candidates: []Candidate{{FoodID: id, Title: title, Food: food}}}
	if e = ApplyCandidate(ctx, fs, i, 0); e != nil {
		return nil, e
	}
	d := &Draft{Day: day, Meal: meal, Items: []*Item{i}, Kind: "barcode"}
	d.Refresh()
	return d, nil
}
func DraftFromProduct(ctx context.Context, fs Foods, p Product, tz, barcode string) (*Draft, error) {
	day, meal, e := Resolve(tz, time.Now(), "", "")
	if e != nil {
		return nil, e
	}
	i := &Item{Name: p.Name, Query: p.Name, Amount: 100, Unit: p.Nutrition.BasisUnit, Status: "pending", Source: p.Source, Confidence: p.Confidence}
	attachLabel(i, &p.Nutrition, p.Brand)
	for _, term := range []string{strings.TrimSpace(p.Brand + " " + p.Name), p.Name} {
		found, err := fs.Search(ctx, term)
		if err != nil {
			var fe *FSError
			if errors.As(err, &fe) {
				break
			}
			return nil, err
		}
		if len(found) > 0 {
			i.Candidates = found
			break
		}
	}
	if e = PickBest(ctx, fs, i); e != nil {
		return nil, e
	}
	d := &Draft{Day: day, Meal: meal, Items: []*Item{i}, Kind: "web", Barcode: barcode}
	d.Refresh()
	return d, nil
}

type WriteReport struct {
	Written, EntryIDs []string
	Failed            [][2]string
	TokenInvalid      bool
}

// The status is committed before the external write. An unconfirmed result is never retried.
func WriteDraft(ctx context.Context, fs Foods, d *Draft, u *User, persist func() error, record func(string) error) (WriteReport, error) {
	r := WriteReport{}
	confirm := func(i *Item, id string) error {
		i.Status, i.EntryID, i.Error, i.EntryWrite = "written", id, "", nil
		if e := record(id); e != nil {
			return e
		}
		r.Written = append(r.Written, first(i.Title, i.Name))
		r.EntryIDs = append(r.EntryIDs, id)
		return nil
	}
	unconfirmed := func(i *Item, e error) {
		reason := "не удалось подтвердить запись; проверь дневник FatSecret"
		if i.EntryWrite != nil {
			reason += " или нажми «Проверить запись» позже"
		}
		r.Failed = append(r.Failed, [2]string{first(i.Title, i.Name), reason})
		var fe *FSError
		r.TokenInvalid = errors.As(e, &fe) && fe.TokenInvalid()
	}
	// Reconcile old attempts before sending any new writes. Otherwise an earlier
	// retryable item could be mistaken for a later item's unconfirmed result.
	for _, i := range d.Items {
		if i.Status != "writing" && i.Status != "unknown" {
			continue
		}
		id, e := reconcileEntry(ctx, fs, u, i)
		if e != nil {
			unconfirmed(i, e)
			return r, nil
		}
		if e = confirm(i, id); e != nil {
			return r, e
		}
	}
	for _, i := range d.Items {
		if i.Status == "written" || i.Status == "undone" {
			continue
		}
		fail := func(reason string) { r.Failed = append(r.Failed, [2]string{first(i.Title, i.Name), reason}) }
		if i.Uncertain() {
			fail("исход предыдущего запроса неизвестен; проверь дневник FatSecret")
			continue
		}
		if i.NeedsPortion {
			fail("сначала выбери порцию")
			continue
		}
		if i.FoodID == "" {
			fail("не найден в базе")
			continue
		}
		if i.ServingID == "" || i.ServingID == "0" || i.Units <= 0 {
			fail("не удалось определить порцию")
			continue
		}
		before, e := fs.Entries(ctx, u, d.Day)
		if e != nil {
			i.Error = e.Error()
			fail("не удалось проверить дневник FatSecret; запись не отправлена")
			var fe *FSError
			r.TokenInvalid = errors.As(e, &fe) && fe.TokenInvalid()
			if e = persist(); e != nil {
				return r, e
			}
			break
		}
		i.EntryWrite = newEntryWrite(d, i, before)
		i.Status = "writing"
		if e := persist(); e != nil {
			return r, e
		}
		id, e := fs.CreateEntry(ctx, u, d, i)
		if e != nil {
			i.Error = e.Error()
			var fe *FSError
			if errors.As(e, &fe) {
				i.Status = "failed"
				i.EntryWrite = nil
				fail(fe.Message)
				if e = persist(); e != nil {
					return r, e
				}
				if fe.TokenInvalid() {
					r.TokenInvalid = true
					break
				}
				continue
			}
			i.Status = "unknown"
			if e = persist(); e != nil {
				return r, e
			}
			id, e = reconcileEntry(ctx, fs, u, i)
			if e != nil {
				unconfirmed(i, e)
				break
			}
		}
		if e = confirm(i, id); e != nil {
			return r, e
		}
	}
	return r, nil
}
func RenderReport(r WriteReport) string {
	var lines []string
	for _, s := range r.Written {
		lines = append(lines, "✅ "+html.EscapeString(s))
	}
	for _, f := range r.Failed {
		lines = append(lines, "❌ "+html.EscapeString(f[0])+" — "+html.EscapeString(f[1]))
	}
	if len(r.Written) > 0 && len(r.Failed) > 0 {
		lines = append(lines, "\nЗаписалось не всё. Повтор коснётся только безопасных для повтора пунктов.")
	}
	if r.TokenInvalid {
		lines = append(lines, "\nДоступ к твоему аккаунту FatSecret отозван — набери /link заново.")
	}
	return first(strings.Join(lines, "\n"), "Нечего записывать.")
}
func ShiftDay(d *Draft, hint, tz string) error {
	if hint != "today" && hint != "yesterday" {
		return fmt.Errorf("неверная дата")
	}
	day, _, e := Resolve(tz, time.Now(), "", hint)
	if e == nil {
		d.Day = day
	}
	return e
}
