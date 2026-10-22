package fsbot

import (
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"
)

type Object = map[string]any

func obj(v any) Object {
	m, _ := v.(map[string]any)
	if m == nil {
		return Object{}
	}
	return m
}
func list(v any) []any {
	switch a := v.(type) {
	case []any:
		return a
	case map[string]any:
		return []any{a}
	}
	return nil
}
func str(v any) string {
	if v == nil {
		return ""
	}
	return fmt.Sprint(v)
}
func num(v any) float64 {
	n, _ := strconv.ParseFloat(str(v), 64)
	if math.IsNaN(n) || math.IsInf(n, 0) {
		return 0
	}
	return n
}
func number(v any) (float64, bool) {
	if v == nil {
		return 0, false
	}
	n, e := strconv.ParseFloat(str(v), 64)
	return n, e == nil && !math.IsNaN(n) && !math.IsInf(n, 0)
}
func round(v float64, places int) float64 { p := math.Pow10(places); return math.RoundToEven(v*p) / p }
func first(values ...string) string {
	for _, v := range values {
		if v != "" {
			return v
		}
	}
	return ""
}
func short(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}
func decodeObject(b []byte) (Object, error) {
	var o Object
	e := json.Unmarshal(b, &o)
	if e == nil && o == nil {
		e = fmt.Errorf("ожидался JSON-объект")
	}
	return o, e
}

type Nutrition struct {
	Kcal      float64 `json:"kcal"`
	Protein   float64 `json:"protein"`
	Fat       float64 `json:"fat"`
	Carbs     float64 `json:"carbs"`
	BasisUnit string  `json:"basis_unit"`
}
type FoodSpec struct {
	Nutrition
	Name  string `json:"name"`
	Brand string `json:"brand"`
}
type Candidate struct {
	FoodID      string `json:"food_id"`
	Title       string `json:"title"`
	Description string `json:"description"`
	Food        Object `json:"food,omitempty"`
}
type ServingChoice struct {
	ID          string `json:"serving_id"`
	Description string `json:"description"`
}
type Item struct {
	Name            string          `json:"name_ru"`
	Query           string          `json:"query"`
	Amount          float64         `json:"amount"`
	Unit            string          `json:"unit"`
	Status          string          `json:"status"`
	EntryID         string          `json:"entry_id,omitempty"`
	Error           string          `json:"error,omitempty"`
	Candidates      []Candidate     `json:"candidates"`
	Chosen          int             `json:"chosen"`
	FoodID          string          `json:"food_id,omitempty"`
	Confidence      float64         `json:"confidence"`
	Creatable       *FoodSpec       `json:"creatable,omitempty"`
	Source          string          `json:"source,omitempty"`
	LabelKcal       float64         `json:"label_kcal,omitempty"`
	LabelMacros     *Nutrition      `json:"label_macros,omitempty"`
	LabelBasis      string          `json:"label_basis_unit,omitempty"`
	ManualServingID string          `json:"manual_serving_id,omitempty"`
	Title           string          `json:"title,omitempty"`
	ServingID       string          `json:"serving_id,omitempty"`
	Units           float64         `json:"units,omitempty"`
	NeedsPortion    bool            `json:"needs_portion,omitempty"`
	Available       []ServingChoice `json:"available_servings,omitempty"`
	Portion         string          `json:"portion,omitempty"`
	Kcal            float64         `json:"kcal"`
	Protein         float64         `json:"protein"`
	Fat             float64         `json:"fat"`
	Carbs           float64         `json:"carbohydrate"`
	Mismatch        *float64        `json:"mismatch,omitempty"`
	OwnFoodID       string          `json:"own_food_id,omitempty"`
	EntryWrite      *EntryWrite     `json:"entry_write,omitempty"`
}

func (i *Item) Locked() bool {
	switch i.Status {
	case "written", "undone", "writing", "unknown", "creating", "create_unknown":
		return true
	}
	return false
}
func (i *Item) Uncertain() bool {
	switch i.Status {
	case "writing", "unknown", "creating", "create_unknown":
		return true
	}
	return false
}

type Draft struct {
	Day            string  `json:"day"`
	Meal           string  `json:"meal"`
	Items          []*Item `json:"items"`
	Kind           string  `json:"kind"`
	Barcode        string  `json:"barcode,omitempty"`
	Confidence     float64 `json:"confidence"`
	NeedsReview    bool    `json:"needs_review"`
	ReviewPrompted bool    `json:"review_prompted,omitempty"`
	CardMessageID  int64   `json:"card_message_id,omitempty"`
}

func (d *Draft) Refresh() {
	d.Confidence = 1
	d.NeedsReview = false
	for _, i := range d.Items {
		d.Confidence = math.Min(d.Confidence, math.Max(0, math.Min(1, i.Confidence)))
		if i.Mismatch != nil {
			d.NeedsReview = true
		}
	}
	d.Confidence = round(d.Confidence, 2)
	d.NeedsReview = d.NeedsReview || d.Confidence < .65
}
func (d *Draft) CanShift() bool {
	for _, i := range d.Items {
		if i.Locked() {
			return false
		}
	}
	return true
}

type RecognizedItem struct {
	Query, Name, Unit, Meal, DateHint, Brand string
	Amount, Confidence                       float64
	Nutrition                                *Nutrition
}
type Recognition struct {
	Kind, Barcode string
	Items         []RecognizedItem
}

var Meals = []string{"breakfast", "lunch", "dinner", "other"}
var MealRU = map[string]string{"breakfast": "завтрак", "lunch": "обед", "dinner": "ужин", "other": "другое"}

func normalizeUnit(v string) string {
	switch strings.ToLower(v) {
	case "ml", "мл", "milliliter", "millilitre":
		return "ml"
	case "piece", "pieces", "pcs", "pc", "шт", "штука":
		return "piece"
	}
	return "g"
}
