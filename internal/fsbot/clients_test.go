package fsbot

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"strings"
	"testing"
)

func testHTTP(t *testing.T, h http.HandlerFunc) *httptest.Server {
	t.Helper()
	s := httptest.NewServer(h)
	t.Cleanup(s.Close)
	return s
}
func TestFatSecretContracts(t *testing.T) {
	ctx := context.Background()
	seen := []url.Values{}
	response := Object{"food_entries": Object{"food_entry": []Object{{"food_entry_id": "1111111"}}}}
	server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" {
			t.Error(r.Method)
		}
		p := r.URL.Query()
		sig := p.Get("oauth_signature")
		p.Del("oauth_signature")
		if OAuthSign("GET", "http://"+r.Host+r.URL.Path, p, "cs", "ts") != sig {
			t.Error("invalid signature")
		}
		seen = append(seen, p)
		json.NewEncoder(w).Encode(response)
	})
	f := NewFatSecret("ck", "cs")
	f.API = server.URL
	u := &User{Token: "token", Secret: "ts"}
	d := readyDraft()
	id, e := f.CreateEntry(ctx, u, d, d.Items[0])
	if e != nil || id != "1111111" {
		t.Fatal(id, e)
	}
	if seen[0].Get("number_of_units") != "1.0000" || seen[0].Get("method") != "food_entry.create" || seen[0].Get("date") != "20724" {
		t.Fatal(seen[0])
	}
	response = Object{"food_id": Object{"value": "77"}}
	id, e = f.CreateFood(ctx, u, FoodSpec{Nutrition{100, 2, 3, 16, "ml"}, "Milk", "Sante"})
	if e != nil || id != "77" {
		t.Fatal(id, e)
	}
	p := seen[1]
	if p.Get("method") != "food.create.v2" || p.Get("serving_amount") != "100" || p.Get("serving_amount_unit") != "ml" || p.Get("metric_serving_amount") != "" {
		t.Fatal(p)
	}
	response = Object{"success": Object{"value": "0"}}
	e = f.DeleteEntry(ctx, u, "42")
	var ue *UnknownOutcome
	if !errors.As(e, &ue) {
		t.Fatal(e)
	}
	response = Object{"success": Object{"value": "1"}}
	if e = f.DeleteEntry(ctx, u, "42"); e != nil {
		t.Fatal(e)
	}
}
func TestFatSecretUnknownAndDefinitiveErrors(t *testing.T) {
	for _, tc := range []struct {
		status  int
		body    string
		unknown bool
	}{{200, `{}`, true}, {200, `{"food_entries":{"food_entry":[{"food_entry_id":"1"},{"food_entry_id":"2"}]}}`, true}, {200, `{"error":{"code":"9","message":"revoked"}}`, false}, {500, `bad json`, true}, {200, `[]`, true}, {200, `{"error":"oops"}`, true}} {
		t.Run(tc.body, func(t *testing.T) {
			server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(tc.status); io.WriteString(w, tc.body) })
			f := NewFatSecret("k", "s")
			f.API = server.URL
			d := readyDraft()
			_, e := f.CreateEntry(context.Background(), &User{}, d, d.Items[0])
			var ue *UnknownOutcome
			if errors.As(e, &ue) != tc.unknown {
				t.Fatal(e)
			}
			if !tc.unknown {
				var fe *FSError
				if !errors.As(e, &fe) || !fe.TokenInvalid() {
					t.Fatal(e)
				}
			}
		})
	}
}
func TestFatSecretSearchBarcodeAndCache(t *testing.T) {
	calls := []string{}
	food := Object{"food_id": "42", "food_name": "Food", "servings": Object{"serving": Object{"serving_id": "1"}}}
	server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		p := r.URL.Query()
		calls = append(calls, p.Get("method"))
		switch p.Get("method") {
		case "foods.search.v5":
			json.NewEncoder(w).Encode(Object{"foods_search": Object{"results": Object{"food": food}}})
		case "food.find_id_for_barcode.v2":
			if p.Get("barcode") != "0036000291452" {
				t.Error(p)
			}
			json.NewEncoder(w).Encode(Object{"food": food})
		case "food.get.v5":
			t.Error("barcode details should be reused")
		case "foods.autocomplete.v2":
			io.WriteString(w, `{"suggestions":{"suggestion":"oats"}}`)
		case "foods.get_recently_eaten.v2":
			io.WriteString(w, `{"error":{"code":12,"message":"tier"}}`)
		}
	})
	f := NewFatSecret("k", "s")
	f.API = server.URL
	found, e := f.Search(context.Background(), "oats")
	if e != nil || len(found) != 1 || found[0].Food == nil {
		t.Fatal(found, e)
	}
	id, e := f.Barcode(context.Background(), "036000291452")
	if e != nil || id != "42" {
		t.Fatal(id, e)
	}
	if _, e = f.Food(context.Background(), id); e != nil {
		t.Fatal(e)
	}
	if id, e = f.Barcode(context.Background(), "00012345600012"); e != nil || id != "" {
		t.Fatal(id, e)
	}
	suggestions, e := f.Autocomplete(context.Background(), "oats")
	if e != nil || !reflect.DeepEqual(suggestions, []string{"oats"}) {
		t.Fatal(suggestions, e)
	}
	if recent, e := f.Recent(context.Background(), &User{}); e != nil || len(recent) != 0 {
		t.Fatal(recent, e)
	}
}
func TestFatSecretOAuthExchange(t *testing.T) {
	server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		r.ParseForm()
		if strings.HasSuffix(r.URL.Path, "request_token") {
			if r.Method != "POST" || r.Form.Get("oauth_callback") != "oob" {
				t.Error(r.Method, r.Form)
			}
			io.WriteString(w, "oauth_token=req%2Btoken&oauth_token_secret=req%26secret")
		} else {
			if r.Method != "GET" || r.Form.Get("oauth_verifier") != "1234" {
				t.Error(r.Method, r.Form)
			}
			io.WriteString(w, "oauth_token=access&oauth_token_secret=secret")
		}
	})
	f := NewFatSecret("k", "s")
	f.AuthBase = server.URL + "/"
	token, secret, auth, e := f.RequestToken(context.Background())
	if e != nil || token != "req+token" || secret != "req&secret" || !strings.Contains(auth, "req%2Btoken") {
		t.Fatal(token, secret, auth, e)
	}
	token, secret, e = f.AccessToken(context.Background(), token, secret, "1234")
	if e != nil || token != "access" || secret != "secret" {
		t.Fatal(token, secret, e)
	}
}

const validRecognition = `{"kind":"text","items":[{"query_en":"apple","name_ru":"яблоко","amount":100,"unit":"g"}]}`

func TestOpenRouterRepairThenFallback(t *testing.T) {
	requests := []Object{}
	server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		var p Object
		json.NewDecoder(r.Body).Decode(&p)
		requests = append(requests, p)
		content := "bad json"
		if p["model"] == "second" {
			content = validRecognition
		}
		json.NewEncoder(w).Encode(Object{"choices": []Object{{"message": Object{"content": content}}}})
	})
	l := NewLLM(Config{TextModels: []string{"first", "second"}})
	l.Endpoint = server.URL
	r, e := l.Text(context.Background(), "яблоко")
	if e != nil || r.Items[0].Name != "яблоко" {
		t.Fatal(r, e)
	}
	if len(requests) != 3 || requests[0]["model"] != "first" || requests[1]["model"] != "first" || requests[2]["model"] != "second" {
		t.Fatal(requests)
	}
	if requests[0]["provider"] == nil || requests[1]["provider"] != nil {
		t.Fatal("schema repair")
	}
}
func TestOpenRouterRateLimitAndEnvelope(t *testing.T) {
	for _, status := range []int{429, 503, 200} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(status)
				io.WriteString(w, `{"choices":[]}`)
			})
			l := NewLLM(Config{TextModels: []string{"model"}})
			l.Endpoint = server.URL
			_, e := l.Text(context.Background(), "food")
			var le *LLMError
			if !errors.As(e, &le) || le.RateLimited() != (status == 429) {
				t.Fatal(e)
			}
			msg := LLMFailure(e, false)
			if strings.Contains(msg, "перегружена") != (status == 429) {
				t.Fatal(msg)
			}
		})
	}
}
func TestLLMParsingNutritionAndOFF(t *testing.T) {
	raw := `{"kind":"label","barcode":"4006381333931","items":[{"query_en":"milk","amount":450,"unit":"ml","nutrition_basis":"100ml","kcal_per_100":60,"protein_per_100":3,"fat_per_100":3.2,"carbs_per_100":4.7}]}`
	r, e := ParseRecognition("```json\n" + raw + "\n```")
	if e != nil || r.Items[0].Nutrition.BasisUnit != "ml" || r.Barcode != "4006381333931" {
		t.Fatal(r, e)
	}
	r, e = ParseRecognition(strings.Replace(raw, `"fat_per_100":3.2,`, "", 1))
	if e != nil || r.Items[0].Nutrition != nil {
		t.Fatal("partial nutrition", r, e)
	}
	for _, s := range []string{`[]`, `{"items":{}}`, `{"items":[]}`, `{"items":[null]}`} {
		if _, e := ParseRecognition(s); e == nil {
			t.Fatal(s)
		}
	}
	p := Object{"status": 1, "product": Object{"product_name": "חומוס", "product_name_en": "Hummus", "brands": "Brand,Other", "product_quantity_unit": "ml", "nutriments": Object{"energy-kcal_100g": 60, "proteins_100g": 3, "fat_100g": 3.2, "carbohydrates_100g": 4.7}}}
	product := ParseOFF(p)
	if product == nil || product.Name != "Hummus" || product.Brand != "Brand" || product.Nutrition.BasisUnit != "ml" {
		t.Fatal(product)
	}
	delete(obj(obj(p["product"])["nutriments"]), "proteins_100g")
	if ParseOFF(p) != nil {
		t.Fatal("partial OFF nutrition")
	}
}

func TestPhotoRecognitionUsesBarcodeAndCaption(t *testing.T) {
	for _, code := range []string{"", "4006381333931"} {
		t.Run(code, func(t *testing.T) {
			requests := []Object{}
			server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				var p Object
				json.NewDecoder(r.Body).Decode(&p)
				requests = append(requests, p)
				content := validRecognition
				if code == "" && len(requests) == 1 {
					content = "label"
				}
				json.NewEncoder(w).Encode(Object{"choices": []Object{{"message": Object{"content": content}}}})
			})
			l := NewLLM(Config{VisionModels: []string{"vision"}})
			l.Endpoint = server.URL
			_, e := l.Photo(context.Background(), []byte("photo"), "150 г", code)
			if e != nil {
				t.Fatal(e)
			}
			want := 1
			if code == "" {
				want = 2
			}
			if len(requests) != want {
				t.Fatal("classification calls", len(requests))
			}
			b, _ := json.Marshal(requests[len(requests)-1])
			if !strings.Contains(string(b), "data:image/jpeg;base64,cGhvdG8=") || !strings.Contains(string(b), "150 г") {
				t.Fatal(string(b))
			}
		})
	}
}
func TestGroundedBarcodeLookupRequiresSourceAndFullNutrition(t *testing.T) {
	for _, extra := range []string{`,"source":"example.org"`, ""} {
		t.Run(extra, func(t *testing.T) {
			server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				var p Object
				json.NewDecoder(r.Body).Decode(&p)
				if p["model"] != "model:online" {
					t.Error(p["model"])
				}
				content := `{"found":true,"name":"Milk","nutrition_basis":"100ml","kcal_per_100":60,"protein_per_100":3,"fat_per_100":3.2,"carbs_per_100":4.7,"confidence":1` + extra + `}`
				json.NewEncoder(w).Encode(Object{"choices": []Object{{"message": Object{"content": content}}}})
			})
			l := NewLLM(Config{TextModels: []string{"model:online"}})
			l.Endpoint = server.URL
			p := l.Lookup(context.Background(), "4006381333931")
			if extra == "" {
				if p != nil {
					t.Fatal("ungrounded product")
				}
			} else if p == nil || p.Confidence != .75 || p.Nutrition.BasisUnit != "ml" {
				t.Fatal(p)
			}
		})
	}
}
