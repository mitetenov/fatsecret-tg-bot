package fsbot

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
)

func TestZAIRecognitionRepair(t *testing.T) {
	requests := []Object{}
	server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" || r.Header.Get("Authorization") != "Bearer zai-key" || r.Header.Get("Content-Type") != "application/json" || r.Header.Get("X-Title") != "" {
			t.Error("incorrect ZAI request headers")
		}
		var p Object
		if e := json.NewDecoder(r.Body).Decode(&p); e != nil {
			t.Error(e)
			return
		}
		requests = append(requests, p)
		if p["model"] != "glm-5.3-flash" || p["provider"] != nil || p["tools"] != nil || obj(p["response_format"])["type"] != "json_object" || obj(p["thinking"])["type"] != "enabled" || p["reasoning_effort"] != "low" {
			t.Error(p)
		}
		content := "bad json"
		if len(requests) == 2 {
			content = validRecognition
		}
		json.NewEncoder(w).Encode(Object{"choices": []Object{{"finish_reason": "stop", "message": Object{"content": content, "reasoning_content": "private reasoning"}}}})
	})
	l := NewLLM(Config{LLMProvider: "zai", ZAIKey: "zai-key", OpenRouterKey: "unused", TextModels: []string{"glm-5.3-flash"}})
	l.Endpoint = server.URL
	r, e := l.Text(context.Background(), "яблоко 100 г")
	if e != nil || len(r.Items) != 1 || r.Items[0].Name != "яблоко" {
		t.Fatal(r, e)
	}
	if len(requests) != 2 {
		t.Fatal("missing repair", requests)
	}
	initial := requests[0]["messages"].([]any)
	repair := requests[1]["messages"].([]any)
	if len(initial) != 2 || obj(initial[0])["role"] != "system" || obj(initial[1])["content"] != "яблоко 100 г" || len(repair) != 3 || obj(repair[2])["content"] != "Верни только валидный JSON." {
		t.Fatal("incorrect recognition or repair messages")
	}
}

func TestZAIPhotoRecognition(t *testing.T) {
	for _, code := range []string{"", "4006381333931"} {
		t.Run(code, func(t *testing.T) {
			requests := []Object{}
			server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				var p Object
				json.NewDecoder(r.Body).Decode(&p)
				requests = append(requests, p)
				if p["model"] != "glm-5.3-flash" || p["provider"] != nil || p["reasoning_effort"] != "high" {
					t.Error(p)
				}
				content := validRecognition
				if code == "" && len(requests) == 1 {
					content = "label"
					if p["response_format"] != nil {
						t.Error("classifier must return plain text")
					}
				} else if obj(p["response_format"])["type"] != "json_object" {
					t.Error("missing JSON mode")
				}
				json.NewEncoder(w).Encode(Object{"choices": []Object{{"finish_reason": "stop", "message": Object{"content": content}}}})
			})
			l := NewLLM(Config{LLMProvider: "zai", ZAIReasoningEffort: "high", VisionModels: []string{"glm-5.3-flash"}})
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
			for _, part := range []string{"data:image/jpeg;base64,cGhvdG8=", "150 г", code} {
				if !strings.Contains(string(b), part) {
					t.Error(string(b))
				}
			}
			messages := requests[len(requests)-1]["messages"].([]any)
			content := obj(messages[0])["content"].([]any)
			if obj(content[0])["text"] != labelPrompt {
				t.Error("incorrect photo prompt")
			}
		})
	}
}

func TestZAIErrors(t *testing.T) {
	for _, tc := range []struct {
		name, body  string
		status      int
		rateLimited bool
	}{
		{"rate limit", `{}`, 429, true},
		{"invalid key", `{"error":{"code":"1000","message":"invalid key"}}`, 401, false},
		{"unavailable", `{}`, 503, false},
		{"missing content", `{"choices":[{"message":{"reasoning_content":"thinking only"}}]}`, 200, false},
		{"empty choices", `{"choices":[]}`, 200, false},
		{"malformed envelope", `not JSON`, 200, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				w.WriteHeader(tc.status)
				io.WriteString(w, tc.body)
			})
			l := NewLLM(Config{LLMProvider: "zai", TextModels: []string{"glm-5.3-flash"}})
			l.Endpoint = server.URL
			_, e := l.Text(context.Background(), "food")
			var le *LLMError
			if !errors.As(e, &le) || le.RateLimited() != tc.rateLimited || calls != 2 {
				t.Fatal(calls, e)
			}
		})
	}
	for _, reason := range []string{"network_error", "length", "sensitive", "tool_calls", "model_context_window_exceeded"} {
		t.Run(reason, func(t *testing.T) {
			server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				json.NewEncoder(w).Encode(Object{"choices": []Object{{"finish_reason": reason, "message": Object{"content": validRecognition}}}})
			})
			l := NewLLM(Config{LLMProvider: "zai", TextModels: []string{"glm-5.3-flash"}})
			l.Endpoint = server.URL
			if _, e := l.Text(context.Background(), "food"); e == nil {
				t.Fatal("accepted incomplete response", reason)
			}
		})
	}
}

func TestZAIBarcodeLookupAndTranslation(t *testing.T) {
	for _, source := range []string{"example.org", ""} {
		t.Run(fmt.Sprintf("source=%s", source), func(t *testing.T) {
			calls := 0
			server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				var p Object
				json.NewDecoder(r.Body).Decode(&p)
				if p["model"] != "glm-5.3-flash" || p["provider"] != nil || obj(p["response_format"])["type"] != "json_object" {
					t.Error(p)
				}
				content := `{"name":"Milk","brand":"Brand"}`
				if calls == 1 {
					tools, _ := p["tools"].([]any)
					if len(tools) != 1 || obj(tools[0])["type"] != "web_search" || obj(obj(tools[0])["web_search"])["enable"] != true || obj(obj(tools[0])["web_search"])["search_engine"] != "search-prime" {
						t.Error("missing native web search", p)
					}
					content = `{"found":true,"name":"რძე","nutrition_basis":"100ml","kcal_per_100":60,"protein_per_100":3,"fat_per_100":3.2,"carbs_per_100":4.7,"confidence":1,"source":"` + source + `"}`
				} else if p["tools"] != nil {
					t.Error("translation should not search the web")
				}
				response := Object{"choices": []Object{{"finish_reason": "stop", "message": Object{"content": content}}}}
				if calls == 1 {
					response["web_search"] = []Object{{"link": "https://example.org/milk", "title": "Milk"}}
				}
				json.NewEncoder(w).Encode(response)
			})
			l := NewLLM(Config{LLMProvider: "zai", TextModels: []string{"glm-5.3-flash"}})
			l.Endpoint = server.URL
			p := l.Lookup(context.Background(), "4006381333931")
			if source == "" {
				if p != nil {
					t.Fatal("accepted product without a source")
				}
				return
			}
			if p == nil || p.Confidence != .75 || p.Nutrition.BasisUnit != "ml" || p.Source != source {
				t.Fatal(p)
			}
			if !l.Translate(context.Background(), p) || p.Name != "Milk" || p.Brand != "Brand" || calls != 2 {
				t.Fatal(p, calls)
			}
		})
	}
}

func TestZAIBarcodeRejectsMissingSearchResults(t *testing.T) {
	server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		content := `{"found":true,"name":"Milk","nutrition_basis":"100ml","kcal_per_100":60,"protein_per_100":3,"fat_per_100":3.2,"carbs_per_100":4.7,"source":"example.org"}`
		json.NewEncoder(w).Encode(Object{"choices": []Object{{"finish_reason": "stop", "message": Object{"content": content}}}})
	})
	l := NewLLM(Config{LLMProvider: "zai", TextModels: []string{"glm-5.3-flash"}})
	l.Endpoint = server.URL
	if p := l.Lookup(context.Background(), "4006381333931"); p != nil {
		t.Fatal("accepted a product without web search results", p)
	}
}
