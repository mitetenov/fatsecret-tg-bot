package fsbot

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"testing"
	"time"
)

func TestPythonParity(t *testing.T) {
	b, e := os.ReadFile("../../testdata/python-parity.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Op       string          `json:"op"`
		Input    json.RawMessage `json:"input"`
		Expected json.RawMessage `json:"expected"`
	}
	if e = json.Unmarshal(b, &cases); e != nil {
		t.Fatal(e)
	}
	for p, c := range cases {
		t.Run(fmt.Sprintf("%s/%d", c.Op, p), func(t *testing.T) {
			var m Object
			if e := json.Unmarshal(c.Input, &m); e != nil {
				t.Fatal(e)
			}
			var actual any
			switch c.Op {
			case "gtin":
				code := str(m["code"])
				actual = Object{"valid": ValidGTIN(code), "canonical": CanonicalGTIN(code, ""), "fatsecret": FatSecretGTIN(code)}
			case "date":
				now, e := time.Parse(time.RFC3339, str(m["now"]))
				if e != nil {
					t.Fatal(e)
				}
				day, meal, e := Resolve(str(m["tz"]), now, "", str(m["hint"]))
				if e != nil {
					t.Fatal(e)
				}
				actual = Object{"day": day, "meal": meal}
			case "nutrition":
				actual = Plausible(Nutrition{Kcal: num(m["kcal"]), Protein: num(m["protein"]), Fat: num(m["fat"]), Carbs: num(m["carbs"])})
			case "parse":
				r, e := ParseRecognition(str(m["raw"]))
				if e == nil {
					actual = Object{"kind": r.Kind, "name": r.Items[0].Name, "amount": r.Items[0].Amount, "unit": r.Items[0].Unit, "confidence": r.Items[0].Confidence}
				}
			case "candidate":
				var i Item
				if e := json.Unmarshal(c.Input, &i); e != nil {
					t.Fatal(e)
				}
				if e := ApplyCandidate(context.Background(), &fakeFoods{}, &i, 0); e != nil {
					t.Fatal(e)
				}
				actual = Object{"food_id": i.FoodID, "serving_id": nil, "units": nil, "kcal": nil, "protein": nil, "fat": nil, "carbohydrate": nil, "needs_portion": i.NeedsPortion, "portion": nil}
				a := actual.(Object)
				if i.ServingID != "" {
					a["serving_id"] = i.ServingID
					a["units"] = i.Units
					a["kcal"] = i.Kcal
					a["protein"] = i.Protein
					a["fat"] = i.Fat
					a["carbohydrate"] = i.Carbs
					a["portion"] = i.Portion
				}
			default:
				t.Fatal("unknown case", c.Op)
			}
			b, e := json.Marshal(actual)
			if e != nil {
				t.Fatal(e)
			}
			var got, want any
			json.Unmarshal(b, &got)
			json.Unmarshal(c.Expected, &want)
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("input=%s\ngot=%s\nwant=%s", c.Input, b, c.Expected)
			}
		})
	}
}
