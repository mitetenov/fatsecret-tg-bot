package fsbot

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha1"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const fatSecretAPI = "https://platform.fatsecret.com/rest/server.api"
const authBase = "https://authentication.fatsecret.com/oauth/"

func percent(s string) string { return strings.ReplaceAll(url.QueryEscape(s), "+", "%20") }
func OAuthBase(method, endpoint string, params url.Values) string {
	u, _ := url.Parse(endpoint)
	scheme := strings.ToLower(u.Scheme)
	host := strings.ToLower(u.Hostname())
	port := u.Port()
	if port != "" && !(port == "80" && scheme == "http" || port == "443" && scheme == "https") {
		host += ":" + port
	}
	path := u.EscapedPath()
	if path == "" {
		path = "/"
	}
	var pairs []string
	for k, vs := range params {
		for _, v := range vs {
			pairs = append(pairs, percent(k)+"="+percent(v))
		}
	}
	sort.Strings(pairs)
	return strings.ToUpper(method) + "&" + percent(scheme+"://"+host+path) + "&" + percent(strings.Join(pairs, "&"))
}
func OAuthSign(method, endpoint string, params url.Values, secret, tokenSecret string) string {
	h := hmac.New(sha1.New, []byte(percent(secret)+"&"+percent(tokenSecret)))
	h.Write([]byte(OAuthBase(method, endpoint, params)))
	return base64.StdEncoding.EncodeToString(h.Sum(nil))
}

type FSError struct {
	Code    int
	Message string
}

func (e *FSError) Error() string      { return e.Message }
func (e *FSError) TokenInvalid() bool { return e.Code == 9 }

type UnknownOutcome struct{ Message string }

func (e *UnknownOutcome) Error() string { return e.Message }
func unknown(msg string) error          { return &UnknownOutcome{msg} }

type FatSecret struct {
	Key, Secret, API, AuthBase string
	HTTP                       *http.Client
	mu                         sync.Mutex
	cache                      map[string]Object
}

func NewFatSecret(key, secret string) *FatSecret {
	return &FatSecret{Key: key, Secret: secret, API: fatSecretAPI, AuthBase: authBase, HTTP: &http.Client{Timeout: 20 * time.Second}, cache: map[string]Object{}}
}
func (f *FatSecret) signed(method, endpoint string, p url.Values, token, secret string) (url.Values, error) {
	out := url.Values{}
	for k, v := range p {
		out[k] = append([]string(nil), v...)
	}
	nonce := make([]byte, 8)
	if _, e := rand.Read(nonce); e != nil {
		return nil, e
	}
	out.Set("oauth_consumer_key", f.Key)
	out.Set("oauth_signature_method", "HMAC-SHA1")
	out.Set("oauth_version", "1.0")
	out.Set("oauth_timestamp", strconv.FormatInt(time.Now().Unix(), 10))
	out.Set("oauth_nonce", hex.EncodeToString(nonce))
	if token != "" {
		out.Set("oauth_token", token)
	}
	out.Set("oauth_signature", OAuthSign(method, endpoint, out, f.Secret, secret))
	return out, nil
}
func (f *FatSecret) Call(ctx context.Context, method, token, secret string, params Object) (Object, error) {
	p := url.Values{"method": {method}, "format": {"json"}}
	for k, v := range params {
		p.Set(k, str(v))
	}
	p, e := f.signed("GET", f.API, p, token, secret)
	if e != nil {
		return nil, e
	}
	req, e := http.NewRequestWithContext(ctx, "GET", f.API+"?"+p.Encode(), nil)
	if e != nil {
		return nil, e
	}
	r, e := f.HTTP.Do(req)
	if e != nil {
		return nil, unknown("Нет подтверждённого ответа FatSecret (сеть или таймаут)")
	}
	defer r.Body.Close()
	b, e := io.ReadAll(io.LimitReader(r.Body, 8<<20))
	if e != nil {
		return nil, unknown("Не удалось прочитать ответ FatSecret")
	}
	payload, e := decodeObject(b)
	if e != nil {
		return nil, unknown("Ответ FatSecret не содержит JSON-объект")
	}
	if raw, ok := payload["error"]; ok {
		m, ok := raw.(map[string]any)
		if !ok {
			return nil, unknown("Неожиданный формат ошибки FatSecret")
		}
		return nil, &FSError{int(num(m["code"])), str(m["message"])}
	}
	if r.StatusCode != 200 {
		return nil, unknown(fmt.Sprintf("Неожиданный ответ FatSecret: HTTP %d", r.StatusCode))
	}
	return payload, nil
}
func (f *FatSecret) auth(ctx context.Context, action, method, token, secret string, extra url.Values) (string, string, error) {
	endpoint := f.AuthBase + action
	p, e := f.signed(method, endpoint, extra, token, secret)
	if e != nil {
		return "", "", e
	}
	var body io.Reader
	if method == "GET" {
		endpoint += "?" + p.Encode()
	} else {
		body = strings.NewReader(p.Encode())
	}
	req, e := http.NewRequestWithContext(ctx, method, endpoint, body)
	if e != nil {
		return "", "", e
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	}
	r, e := f.HTTP.Do(req)
	if e != nil {
		return "", "", fmt.Errorf("FatSecret не ответил на запрос авторизации")
	}
	defer r.Body.Close()
	b, e := io.ReadAll(io.LimitReader(r.Body, 64<<10))
	if e != nil {
		return "", "", e
	}
	values, e := url.ParseQuery(string(b))
	if e != nil || r.StatusCode != 200 || values.Get("oauth_token") == "" || values.Get("oauth_token_secret") == "" {
		return "", "", fmt.Errorf("FatSecret не принял авторизацию — проверь PIN или начни /link заново")
	}
	return values.Get("oauth_token"), values.Get("oauth_token_secret"), nil
}
func (f *FatSecret) RequestToken(ctx context.Context) (string, string, string, error) {
	t, s, e := f.auth(ctx, "request_token", "POST", "", "", url.Values{"oauth_callback": {"oob"}})
	return t, s, f.AuthBase + "authorize?oauth_token=" + url.QueryEscape(t), e
}
func (f *FatSecret) AccessToken(ctx context.Context, t, s, pin string) (string, string, error) {
	return f.auth(ctx, "access_token", "GET", t, s, url.Values{"oauth_verifier": {pin}})
}
func foodCandidates(raw any) []Candidate {
	var out []Candidate
	for _, v := range list(raw) {
		m := obj(v)
		id := str(m["food_id"])
		if id == "" {
			continue
		}
		c := Candidate{FoodID: id, Title: strings.TrimSpace(str(m["brand_name"]) + " " + str(m["food_name"])), Description: str(m["food_description"])}
		if m["servings"] != nil {
			c.Food = m
		}
		out = append(out, c)
	}
	return out
}
func (f *FatSecret) Search(ctx context.Context, query string) ([]Candidate, error) {
	p, e := f.Call(ctx, "foods.search.v5", "", "", Object{"search_expression": query, "max_results": 5, "flag_default_serving": "True"})
	if e != nil {
		return nil, e
	}
	raw := obj(obj(p["foods_search"])["results"])["food"]
	if raw == nil {
		raw = obj(p["foods"])["food"]
	}
	return foodCandidates(raw), nil
}
func (f *FatSecret) Food(ctx context.Context, id string) (Object, error) {
	f.mu.Lock()
	cached := f.cache[id]
	delete(f.cache, id)
	f.mu.Unlock()
	if cached != nil {
		return cached, nil
	}
	p, e := f.Call(ctx, "food.get.v5", "", "", Object{"food_id": id, "flag_default_serving": "True"})
	if e != nil {
		return nil, e
	}
	food := obj(p["food"])
	if len(food) == 0 {
		return nil, unknown("FatSecret не вернул продукт")
	}
	return food, nil
}
func (f *FatSecret) Autocomplete(ctx context.Context, q string) ([]string, error) {
	p, e := f.Call(ctx, "foods.autocomplete.v2", "", "", Object{"expression": q, "max_results": 4})
	var fsErr *FSError
	if errors.As(e, &fsErr) {
		return nil, nil
	}
	if e != nil {
		return nil, e
	}
	v := obj(p["suggestions"])["suggestion"]
	if s, ok := v.(string); ok {
		return []string{s}, nil
	}
	var out []string
	for _, v := range list(v) {
		out = append(out, str(v))
	}
	return out, nil
}
func (f *FatSecret) Barcode(ctx context.Context, code string) (string, error) {
	code = FatSecretGTIN(code)
	if code == "" {
		return "", nil
	}
	p, e := f.Call(ctx, "food.find_id_for_barcode.v2", "", "", Object{"barcode": code, "flag_default_serving": "True"})
	var fsErr *FSError
	if errors.As(e, &fsErr) && fsErr.Code == 211 {
		return "", nil
	}
	if e != nil {
		return "", e
	}
	food := obj(p["food"])
	id := first(str(food["food_id"]), str(obj(p["food_id"])["value"]))
	if id == "0" {
		return "", nil
	}
	if id != "" && food["servings"] != nil {
		f.mu.Lock()
		if len(f.cache) > 128 {
			clear(f.cache)
		}
		f.cache[id] = food
		f.mu.Unlock()
	}
	return id, nil
}
func (f *FatSecret) Recent(ctx context.Context, u *User) ([]Candidate, error) {
	p, e := f.Call(ctx, "foods.get_recently_eaten.v2", u.Token, u.Secret, nil)
	var fe *FSError
	if errors.As(e, &fe) {
		return nil, nil
	}
	if e != nil {
		return nil, e
	}
	return foodCandidates(obj(p["foods"])["food"]), nil
}
func (f *FatSecret) CreateFood(ctx context.Context, u *User, spec FoodSpec) (string, error) {
	if !Plausible(spec.Nutrition) {
		return "", fmt.Errorf("неполные или неправдоподобные КБЖУ")
	}
	basis := first(spec.BasisUnit, "g")
	p, e := f.Call(ctx, "food.create.v2", u.Token, u.Secret, Object{"food_name": short(spec.Name, 60), "brand_type": "manufacturer", "brand_name": short(spec.Brand, 60), "serving_size": "100 " + basis, "serving_amount": 100, "serving_amount_unit": basis, "calories": spec.Kcal, "protein": spec.Protein, "fat": spec.Fat, "carbohydrate": spec.Carbs})
	if e != nil {
		return "", e
	}
	id := str(obj(p["food_id"])["value"])
	if id == "" || id == "0" {
		return "", unknown("FatSecret не вернул ID созданного продукта")
	}
	return id, nil
}
func (f *FatSecret) CreateEntry(ctx context.Context, u *User, d *Draft, i *Item) (string, error) {
	day, e := FatSecretDate(d.Day)
	if e != nil || MealRU[d.Meal] == "" {
		return "", fmt.Errorf("неверная дата или приём пищи")
	}
	p, e := f.Call(ctx, "food_entry.create", u.Token, u.Secret, Object{"food_id": i.FoodID, "serving_id": i.ServingID, "number_of_units": fmt.Sprintf("%.4f", i.Units), "food_entry_name": short(first(i.Title, i.Name), 60), "meal": d.Meal, "date": day})
	if e != nil {
		return "", e
	}
	entries := list(obj(p["food_entries"])["food_entry"])
	if len(entries) != 1 || str(obj(entries[0])["food_entry_id"]) == "" || str(obj(entries[0])["food_entry_id"]) == "0" {
		return "", unknown("FatSecret не вернул ID записи в дневнике")
	}
	return str(obj(entries[0])["food_entry_id"]), nil
}
func (f *FatSecret) DeleteEntry(ctx context.Context, u *User, id string) error {
	p, e := f.Call(ctx, "food_entry.delete", u.Token, u.Secret, Object{"food_entry_id": id})
	if e != nil {
		return e
	}
	if str(obj(p["success"])["value"]) != "1" {
		return unknown("FatSecret не подтвердил удаление записи")
	}
	return nil
}
