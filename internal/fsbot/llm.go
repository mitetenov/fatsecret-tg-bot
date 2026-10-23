package fsbot

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math"
	"net/http"
	"net/url"
	"strings"
	"time"
)

func ExtractJSON(raw string) (Object, error) {
	raw = strings.TrimSpace(raw)
	if p, e := decodeObject([]byte(raw)); e == nil {
		return p, nil
	}
	a, b := strings.Index(raw, "{"), strings.LastIndex(raw, "}")
	if a < 0 || b < a {
		return nil, fmt.Errorf("в ответе модели нет JSON")
	}
	return decodeObject([]byte(raw[a : b+1]))
}
func nutritionFrom(m Object) *Nutrition {
	basis := "g"
	b := strings.ReplaceAll(strings.ToLower(str(m["nutrition_basis"])), " ", "")
	if b == "ml" || b == "100ml" {
		basis = "ml"
	}
	ns := []float64{}
	for _, key := range []string{"kcal", "protein", "fat", "carbs"} {
		v, ok := m[key+"_per_100"]
		if !ok {
			v = m[key+"_100"+basis]
		}
		n, ok := number(v)
		if !ok {
			return nil
		}
		ns = append(ns, n)
	}
	n := &Nutrition{ns[0], ns[1], ns[2], ns[3], basis}
	if !Plausible(*n) {
		return nil
	}
	return n
}
func ParseRecognition(raw string) (Recognition, error) {
	p, e := ExtractJSON(raw)
	if e != nil {
		return Recognition{}, e
	}
	r := Recognition{Kind: str(p["kind"])}
	switch r.Kind {
	case "text", "plate", "label":
	default:
		r.Kind = "text"
	}
	entries, ok := p["items"].([]any)
	if !ok {
		return r, fmt.Errorf("модель не вернула список продуктов")
	}
	for _, v := range entries {
		m := obj(v)
		q := strings.TrimSpace(first(str(m["query_en"]), str(m["name_ru"])))
		amount, ok := number(m["amount"])
		if q == "" || !ok || amount <= 0 || round(amount, 2) <= 0 {
			continue
		}
		def := .8
		switch r.Kind {
		case "label":
			def = .6
		case "plate":
			def = .55
		}
		confidence, ok := number(m["confidence"])
		if !ok {
			confidence = def
		}
		confidence = round(math.Max(0, math.Min(1, confidence)), 2)
		meal := strings.ToLower(str(m["meal"]))
		if MealRU[meal] == "" {
			meal = ""
		}
		hint := strings.ToLower(str(m["date_hint"]))
		if hint != "today" && hint != "yesterday" {
			hint = ""
		}
		r.Items = append(r.Items, RecognizedItem{q, strings.TrimSpace(first(str(m["name_ru"]), q)), normalizeUnit(str(m["unit"])), meal, hint, strings.TrimSpace(str(m["brand"])), round(amount, 2), confidence, nutritionFrom(m)})
	}
	if len(r.Items) == 0 {
		return r, fmt.Errorf("модель не назвала ни одного продукта")
	}
	barcode := str(p["barcode"])
	valid := true
	for _, c := range barcode {
		if (c < '0' || c > '9') && c != ' ' && c != '-' {
			valid = false
		}
	}
	if valid {
		r.Barcode = CanonicalGTIN(barcode, "")
	}
	return r, nil
}

type LLMError struct {
	Statuses []int
	Message  string
}

func (e *LLMError) Error() string { return e.Message }
func (e *LLMError) RateLimited() bool {
	if len(e.Statuses) == 0 {
		return false
	}
	for _, s := range e.Statuses {
		if s != 429 {
			return false
		}
	}
	return true
}

type LLM struct {
	Key, Endpoint             string
	Provider, ReasoningEffort string
	TextModels, VisionModels  []string
	HTTP                      *http.Client
}

func NewLLM(c Config) *LLM {
	l := &LLM{
		Key: c.OpenRouterKey, Endpoint: "https://openrouter.ai/api/v1/chat/completions",
		Provider: first(c.LLMProvider, "openrouter"), TextModels: c.TextModels, VisionModels: c.VisionModels,
		HTTP: &http.Client{Timeout: 90 * time.Second},
	}
	if l.Provider == "zai" {
		l.Key, l.Endpoint = c.ZAIKey, "https://api.z.ai/api/paas/v4/chat/completions"
		l.ReasoningEffort = first(c.ZAIReasoningEffort, "low")
	}
	return l
}

type llmOptions struct {
	Structured, WebSearch bool
}

func (l *LLM) one(ctx context.Context, model string, messages []Object, options llmOptions) (string, error) {
	if options.WebSearch && l.Provider != "zai" && !strings.HasSuffix(model, ":online") {
		model += ":online"
	}
	body := Object{"model": model, "messages": messages}
	if l.Provider == "zai" {
		body["thinking"] = Object{"type": "enabled"}
		body["reasoning_effort"] = l.ReasoningEffort
		if options.Structured {
			body["response_format"] = Object{"type": "json_object"}
		}
		if options.WebSearch {
			body["tools"] = []Object{{"type": "web_search", "web_search": Object{"enable": true, "search_engine": "search-prime", "search_result": true}}}
		}
	} else if options.Structured {
		body["response_format"] = Object{"type": "json_schema", "json_schema": json.RawMessage(recognitionSchemaJSON)}
		body["provider"] = Object{"require_parameters": true}
	}
	b, e := json.Marshal(body)
	if e != nil {
		return "", e
	}
	req, e := http.NewRequestWithContext(ctx, "POST", l.Endpoint, bytes.NewReader(b))
	if e != nil {
		return "", e
	}
	req.Header.Set("Authorization", "Bearer "+l.Key)
	if l.Provider != "zai" {
		req.Header.Set("X-Title", "fsbot")
	}
	req.Header.Set("Content-Type", "application/json")
	resp, e := l.HTTP.Do(req)
	if e != nil {
		return "", &LLMError{[]int{0}, model + ": нет ответа провайдера"}
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return "", &LLMError{[]int{resp.StatusCode}, fmt.Sprintf("%s: HTTP %d", model, resp.StatusCode)}
	}
	b, e = io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if e != nil {
		return "", &LLMError{[]int{0}, model + ": не удалось прочитать ответ"}
	}
	p, e := decodeObject(b)
	if e == nil {
		if l.Provider == "zai" && options.WebSearch {
			results, _ := p["web_search"].([]any)
			if len(results) == 0 {
				return "", &LLMError{[]int{0}, model + ": нет результатов веб-поиска"}
			}
		}
		choices, _ := p["choices"].([]any)
		if len(choices) > 0 {
			if l.Provider == "zai" {
				reason := str(obj(choices[0])["finish_reason"])
				if reason != "" && reason != "stop" {
					return "", &LLMError{[]int{0}, model + ": модель не завершила ответ"}
				}
			}
			content, ok := obj(obj(choices[0])["message"])["content"].(string)
			if ok {
				return content, nil
			}
		}
	}
	return "", &LLMError{[]int{0}, model + ": ответ не содержит текст"}
}
func failureStatuses(e error) []int {
	var le *LLMError
	if errors.As(e, &le) {
		return le.Statuses
	}
	return []int{0}
}
func (l *LLM) complete(ctx context.Context, models []string, messages []Object, options llmOptions) (string, error) {
	statuses := []int{}
	for _, m := range models {
		s, e := l.one(ctx, m, messages, options)
		if e == nil {
			return s, nil
		}
		statuses = append(statuses, failureStatuses(e)...)
		if ctx.Err() != nil {
			break
		}
	}
	return "", &LLMError{statuses, "нет доступных моделей"}
}
func (l *LLM) recognize(ctx context.Context, models []string, messages []Object) (Recognition, error) {
	statuses := []int{}
	for _, m := range models {
		for attempt := 0; attempt < 2; attempt++ {
			msgs := messages
			if attempt == 1 {
				msgs = append(append([]Object(nil), messages...), Object{"role": "user", "content": "Верни только валидный JSON."})
			}
			raw, e := l.one(ctx, m, msgs, llmOptions{Structured: attempt == 0 || l.Provider == "zai"})
			if e == nil {
				var r Recognition
				r, e = ParseRecognition(raw)
				if e == nil {
					return r, nil
				}
			}
			statuses = append(statuses, failureStatuses(e)...)
			slog.Warn("модель не прошла распознавание", "model", m, "attempt", attempt+1, "error", e)
			if ctx.Err() != nil {
				return Recognition{}, ctx.Err()
			}
		}
	}
	return Recognition{}, &LLMError{statuses, "не удалось разобрать ответ моделей"}
}
func (l *LLM) Text(ctx context.Context, s string) (Recognition, error) {
	return l.recognize(ctx, l.TextModels, []Object{{"role": "system", "content": textPrompt}, {"role": "user", "content": s}})
}
func (l *LLM) Photo(ctx context.Context, image []byte, caption, barcode string) (Recognition, error) {
	data := "data:image/jpeg;base64," + base64.StdEncoding.EncodeToString(image)
	kind := "label"
	imagePart := Object{"type": "image_url", "image_url": Object{"url": data}}
	if barcode == "" {
		answer, _ := l.complete(ctx, l.VisionModels, []Object{{"role": "user", "content": []Object{{"type": "text", "text": classifyPrompt}, imagePart}}}, llmOptions{})
		kind = "plate"
		if strings.Contains(strings.ToLower(answer), "label") {
			kind = "label"
		}
	}
	prompt := platePrompt
	if kind == "label" {
		prompt = labelPrompt
	}
	content := []Object{{"type": "text", "text": prompt}, imagePart}
	if barcode != "" {
		content = append(content, Object{"type": "text", "text": "На упаковке штрих-код " + barcode + ". КБЖУ бери только с фото."})
	}
	if caption != "" {
		content = append(content, Object{"type": "text", "text": "Подпись пользователя: " + caption})
	}
	return l.recognize(ctx, l.VisionModels, []Object{{"role": "user", "content": content}})
}

type Product struct {
	Name, Brand, Source string
	Nutrition           Nutrition
	Confidence          float64
}

func (l *LLM) Lookup(ctx context.Context, code string) *Product {
	prompt := strings.ReplaceAll(barcodePrompt, "{barcode}", code)
	prompt = strings.ReplaceAll(strings.ReplaceAll(prompt, "{{", "{"), "}}", "}")
	for attempt := 0; attempt < 3; attempt++ {
		raw, e := l.complete(ctx, l.TextModels, []Object{{"role": "user", "content": prompt}}, llmOptions{Structured: l.Provider == "zai", WebSearch: true})
		if e != nil {
			return nil
		}
		m, e := ExtractJSON(raw)
		if e != nil {
			return nil
		}
		found, _ := m["found"].(bool)
		if !found {
			continue
		}
		n := nutritionFrom(m)
		name, source := strings.TrimSpace(str(m["name"])), strings.TrimSpace(str(m["source"]))
		if n == nil || name == "" || source == "" {
			return nil
		}
		confidence, ok := number(m["confidence"])
		if !ok {
			confidence = .6
		}
		return &Product{name, first(str(m["brand"]), "fsbot"), source, *n, round(math.Max(0, math.Min(.75, confidence)), 2)}
	}
	return nil
}
func (l *LLM) Translate(ctx context.Context, p *Product) bool {
	prompt := strings.ReplaceAll(strings.ReplaceAll(translatePrompt, "{name}", p.Name), "{brand}", p.Brand)
	prompt = strings.ReplaceAll(strings.ReplaceAll(prompt, "{{", "{"), "}}", "}")
	raw, e := l.complete(ctx, l.TextModels, []Object{{"role": "user", "content": prompt}}, llmOptions{Structured: l.Provider == "zai"})
	if e != nil {
		return false
	}
	m, e := ExtractJSON(raw)
	if e != nil {
		return false
	}
	name := strings.TrimSpace(StripBidi(str(m["name"])))
	if !Readable(name) {
		return false
	}
	p.Name = name
	p.Brand = first(strings.TrimSpace(StripBidi(str(m["brand"]))), p.Brand)
	return true
}
func ParseOFF(p Object) *Product {
	if num(p["status"]) != 1 {
		return nil
	}
	m := obj(p["product"])
	name := PickName(str(m["product_name_ru"]), str(m["product_name_en"]), str(m["product_name"]))
	if name == "" {
		return nil
	}
	raw := obj(m["nutriments"])
	v := []float64{}
	for _, key := range []string{"energy-kcal_100g", "proteins_100g", "fat_100g", "carbohydrates_100g"} {
		n, ok := number(raw[key])
		if !ok {
			return nil
		}
		v = append(v, n)
	}
	basis := "g"
	declared := strings.ReplaceAll(strings.ToLower(str(m["nutrition_data_per"])), " ", "")
	unit := strings.ToLower(str(m["product_quantity_unit"]))
	if declared == "100ml" || unit == "ml" || unit == "cl" || unit == "l" {
		basis = "ml"
	}
	n := Nutrition{v[0], v[1], v[2], v[3], basis}
	if !Plausible(n) {
		return nil
	}
	brand := PickName(strings.Split(str(m["brands_en"]), ",")[0], strings.Split(str(m["brands"]), ",")[0])
	return &Product{name, first(brand, "fsbot"), "openfoodfacts.org", n, .9}
}

type FoodFacts struct {
	HTTP     *http.Client
	Endpoint string
}

func NewFoodFacts() *FoodFacts {
	return &FoodFacts{&http.Client{Timeout: 15 * time.Second}, "https://world.openfoodfacts.org/api/v2/product/"}
}
func (f *FoodFacts) Lookup(ctx context.Context, code string) *Product {
	endpoint := f.Endpoint + url.PathEscape(code) + ".json?fields=product_name,product_name_ru,product_name_en,brands,brands_en,nutriments,nutrition_data_per,product_quantity_unit"
	req, e := http.NewRequestWithContext(ctx, "GET", endpoint, nil)
	if e != nil {
		return nil
	}
	req.Header.Set("User-Agent", "fsbot/0.2 (https://github.com/mitetenov/fatsecret-tg-bot)")
	r, e := f.HTTP.Do(req)
	if e != nil {
		return nil
	}
	defer r.Body.Close()
	if r.StatusCode != 200 {
		return nil
	}
	b, e := io.ReadAll(io.LimitReader(r.Body, 4<<20))
	if e != nil {
		return nil
	}
	p, e := decodeObject(b)
	if e != nil {
		return nil
	}
	return ParseOFF(p)
}
func LLMFailure(e error, photo bool) string {
	var le *LLMError
	if errors.As(e, &le) && le.RateLimited() {
		return "Модель сейчас перегружена на стороне провайдера — попробуй ещё раз через минуту. Переписывать ничего не нужно, дело не в тебе."
	}
	if photo {
		return "Не смог разобрать фото. Напиши текстом, что съел."
	}
	return "Не смог разобрать. Напиши иначе — например «овсянка 60 г»."
}
