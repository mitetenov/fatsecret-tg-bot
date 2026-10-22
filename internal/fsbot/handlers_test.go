package fsbot

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

type harness struct {
	app             *App
	mu              sync.Mutex
	calls           []Object
	fsCalls         []string
	writes, deletes int
	createMode      string
	entryMode       string
	diary           []Object
	hideNewEntries  bool
}

func newHarness(t *testing.T) *harness {
	t.Helper()
	s := openTestStore(t)
	c := Config{OwnerID: 42, DefaultTZ: "UTC", TextModels: []string{"first"}, VisionModels: []string{"vision"}, StateDir: t.TempDir()}
	h := &harness{app: NewApp(c, s)}
	if _, e := s.EnsureUser(42); e != nil {
		t.Fatal(e)
	}
	if e := s.SaveLink(42, "test-token", "test-secret"); e != nil {
		t.Fatal(e)
	}
	server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		h.mu.Lock()
		defer h.mu.Unlock()
		if r.URL.Path == "/fs" {
			r.ParseForm()
			p := r.Form
			method := p.Get("method")
			h.fsCalls = append(h.fsCalls, method)
			switch method {
			case "food_entries.get.v2":
				entries := h.diary
				if h.hideNewEntries {
					entries = nil
				}
				if entries == nil {
					entries = []Object{}
				}
				json.NewEncoder(w).Encode(Object{"food_entries": Object{"food_entry": entries}})
			case "food_entry.create":
				h.writes++
				h.diary = append(h.diary, Object{"food_entry_id": fmt.Sprint(h.writes), "food_id": p.Get("food_id"), "serving_id": p.Get("serving_id"), "number_of_units": p.Get("number_of_units"), "date_int": p.Get("date"), "meal": p.Get("meal"), "food_entry_name": p.Get("food_entry_name")})
				if h.entryMode == "lost-response" {
					conn, _, e := w.(http.Hijacker).Hijack()
					if e != nil {
						t.Error(e)
						return
					}
					conn.Close()
					return
				}
				if h.entryMode == "missing-id" {
					io.WriteString(w, `{}`)
					return
				}
				if h.entryMode == "ambiguous" {
					another := Object{}
					for k, v := range h.diary[len(h.diary)-1] {
						another[k] = v
					}
					another["food_entry_id"] = "200"
					h.diary = append(h.diary, another)
					io.WriteString(w, `{}`)
					return
				}
				if h.entryMode == "full-diary" {
					json.NewEncoder(w).Encode(Object{"food_entries": Object{"food_entry": h.diary}})
					return
				}
				if h.entryMode == "invalid-json" {
					io.WriteString(w, `<html>upstream error</html>`)
					return
				}
				json.NewEncoder(w).Encode(Object{"food_entries": Object{"food_entry": Object{"food_entry_id": fmt.Sprint(h.writes)}}})
			case "food_entry.delete":
				h.deletes++
				io.WriteString(w, `{"success":{"value":"1"}}`)
			case "food.create.v2":
				if h.createMode == "unknown" {
					io.WriteString(w, `{}`)
				} else {
					io.WriteString(w, `{"food_id":{"value":"77"}}`)
				}
			case "food.get.v5":
				if h.createMode == "get-fails" {
					w.WriteHeader(503)
					io.WriteString(w, `{}`)
				} else {
					json.NewEncoder(w).Encode(Object{"food": sampleFood()})
				}
			case "foods.search.v5":
				json.NewEncoder(w).Encode(Object{"foods_search": Object{"results": Object{"food": sampleFood()}}})
			default:
				io.WriteString(w, `{}`)
			}
			return
		}
		var p Object
		json.NewDecoder(r.Body).Decode(&p)
		if r.URL.Path == "/llm" {
			json.NewEncoder(w).Encode(Object{"choices": []Object{{"message": Object{"content": validRecognition}}}})
			return
		}
		p["method"] = strings.TrimPrefix(r.URL.Path, "/bottest/")
		h.calls = append(h.calls, p)
		if strings.HasSuffix(r.URL.Path, "getUpdates") {
			io.WriteString(w, `{"ok":false,"error_code":409,"description":"Conflict: terminated by other getUpdates"}`)
			return
		}
		json.NewEncoder(w).Encode(Object{"ok": true, "result": Object{"message_id": 100}})
	})
	h.app.TG.Token = "test"
	h.app.TG.Base = server.URL
	h.app.FS.API = server.URL + "/fs"
	h.app.LLM.Endpoint = server.URL + "/llm"
	return h
}
func sampleFood() Object {
	return Object{"food_id": "77", "food_name": "Milk", "servings": Object{"serving": Object{"serving_id": "771", "serving_description": "100 ml", "metric_serving_amount": "100", "metric_serving_unit": "ml", "number_of_units": "100", "calories": "60", "protein": "3", "fat": "3.2", "carbohydrate": "4.7"}}}
}
func (h *harness) message(text string) Update {
	return Update{Message: &Message{ID: 5, From: TGUser{42}, Chat: Chat{42, "private"}, Text: text}}
}
func (h *harness) callback(id int64, action, arg string) Update {
	return Update{Callback: &Callback{ID: "c", From: TGUser{42}, Message: &Message{ID: 100, Chat: Chat{42, "private"}}, Data: button(id, action, arg, "").Data}}
}
func (h *harness) send(t *testing.T, u Update) {
	t.Helper()
	if e := h.app.Handle(context.Background(), u); e != nil {
		t.Fatal(e)
	}
}
func (h *harness) lastAnswer() string {
	h.mu.Lock()
	defer h.mu.Unlock()
	for p := len(h.calls) - 1; p >= 0; p-- {
		if h.calls[p]["method"] == "answerCallbackQuery" {
			return str(h.calls[p]["text"])
		}
	}
	return ""
}
func TestTextEditWriteUndoFlow(t *testing.T) {
	h := newHarness(t)
	h.send(t, h.message("яблоко"))
	id, d, e := h.app.Store.LastDraft(42)
	if e != nil || d == nil || d.Items[0].FoodID != "77" {
		t.Fatal(d, e)
	}
	h.send(t, h.message("450 мл"))
	d, e = h.app.Store.Draft(id, 42)
	if e != nil || d.Items[0].Amount != 450 || d.Items[0].Kcal != 270 || d.CardMessageID != 100 {
		t.Fatal(d, e)
	}
	h.send(t, h.callback(id, "w", ""))
	if h.writes != 1 {
		t.Fatal(h.writes)
	}
	h.send(t, h.callback(id, "w", ""))
	if h.writes != 1 {
		t.Fatal("stale button wrote twice")
	}
	h.send(t, h.message("/undo"))
	if h.deletes != 1 {
		t.Fatal(h.deletes)
	}
	h.send(t, h.message("/undo"))
	if h.deletes != 1 {
		t.Fatal("undo repeated")
	}
}
func TestReviewCannotBeBypassed(t *testing.T) {
	h := newHarness(t)
	d := readyDraft()
	d.Items = d.Items[:1]
	d.NeedsReview = true
	d.Confidence = .55
	id, e := h.app.Store.SaveDraft(42, d)
	if e != nil {
		t.Fatal(e)
	}
	h.send(t, h.callback(id, "w", ""))
	if h.writes != 0 || h.lastAnswer() != "Нужна дополнительная проверка" {
		t.Fatal(h.lastAnswer())
	}
	h.send(t, h.callback(id, "w", ""))
	if h.writes != 1 {
		t.Fatal(h.writes)
	}
}
func TestPrivateAndOwnerFilters(t *testing.T) {
	h := newHarness(t)
	d := readyDraft()
	id, e := h.app.Store.SaveDraft(42, d)
	if e != nil {
		t.Fatal(e)
	}
	u := h.callback(id, "w", "")
	u.Callback.Message.Chat.Type = "group"
	h.send(t, u)
	if h.writes != 0 {
		t.Fatal("group write")
	}
	u = h.callback(id, "w", "")
	u.Callback.From.ID = 99
	h.send(t, u)
	if h.writes != 0 {
		t.Fatal("cross owner write")
	}
	u = h.message("/allow 99")
	u.Message.From.ID = 99
	u.Message.Chat.ID = 99
	h.send(t, u)
	user, e := h.app.Store.User(99)
	if e != nil || user.Allowed {
		t.Fatal(user, e)
	}
	h.send(t, h.message("/allow 99"))
	user, e = h.app.Store.User(99)
	if e != nil || !user.Allowed {
		t.Fatal(user, e)
	}
}
func TestMalformedAndLockedCallbacks(t *testing.T) {
	h := newHarness(t)
	d := readyDraft()
	d.Items[0].Status = "unknown"
	id, e := h.app.Store.SaveDraft(42, d)
	if e != nil {
		t.Fatal(e)
	}
	for _, tc := range [][2]string{{"g", "0"}, {"c", "0.0"}, {"s", "0.1"}, {"n", "0"}, {"g", "999"}, {"m", "bad"}, {"d", "bogus"}} {
		h.send(t, h.callback(id, tc[0], tc[1]))
	}
	if h.writes != 0 {
		t.Fatal(h.writes)
	}
	stored, e := h.app.Store.Draft(id, 42)
	if e != nil || stored.Items[0].Status != "unknown" || stored.Meal != "lunch" || stored.Day != d.Day {
		t.Fatal(stored, e)
	}
	u := h.callback(id, "w", "")
	u.Callback.Data = "invalid"
	h.send(t, u)
	if h.lastAnswer() != "Неизвестная кнопка" {
		t.Fatal(h.lastAnswer())
	}
}
func TestOwnFoodCreationRecovery(t *testing.T) {
	for _, mode := range []string{"ok", "unknown", "get-fails"} {
		t.Run(mode, func(t *testing.T) {
			h := newHarness(t)
			h.createMode = mode
			d := &Draft{Day: "2026-09-28", Meal: "lunch", Barcode: "0036000291452", Items: []*Item{{Name: "Milk", Amount: 450, Unit: "ml", Confidence: .9, Status: "pending", Creatable: &FoodSpec{Nutrition{60, 3, 3.2, 4.7, "ml"}, "Milk", "Sante"}}}}
			id, e := h.app.Store.SaveDraft(42, d)
			if e != nil {
				t.Fatal(e)
			}
			h.send(t, h.callback(id, "n", "0"))
			stored, e := h.app.Store.Draft(id, 42)
			if e != nil {
				t.Fatal(e)
			}
			item := stored.Items[0]
			binding, e := h.app.Store.BoundFood(42, d.Barcode)
			if e != nil {
				t.Fatal(e)
			}
			if mode == "unknown" {
				if item.Status != "create_unknown" || binding != "" {
					t.Fatal(item, binding)
				}
				h.send(t, h.callback(id, "n", "0"))
			} else {
				if item.OwnFoodID != "77" || item.Creatable != nil || binding != "77" {
					t.Fatal(item, binding)
				}
				if mode == "ok" && item.Kcal != 270 {
					t.Fatal(item)
				}
				if mode == "get-fails" {
					h.createMode = "ok"
					h.send(t, h.callback(id, "c", "0.0"))
					stored, _ = h.app.Store.Draft(id, 42)
					if stored.Items[0].FoodID != "77" {
						t.Fatal(stored)
					}
				}
			}
			count := 0
			for _, s := range h.fsCalls {
				if s == "food.create.v2" {
					count++
				}
			}
			if count != 1 {
				t.Fatal("creation repeated", h.fsCalls)
			}
		})
	}
}
func TestCancelPreservesUndo(t *testing.T) {
	h := newHarness(t)
	d := readyDraft()
	d.Items[0].Status = "written"
	d.Items[0].EntryID = "1"
	d.Items[1].Status = "unknown"
	id, e := h.app.Store.SaveDraft(42, d)
	if e != nil {
		t.Fatal(e)
	}
	if e = h.app.Store.RecordWrite(id, 42, d, "1"); e != nil {
		t.Fatal(e)
	}
	h.send(t, h.callback(id, "x", ""))
	h.send(t, h.message("/undo"))
	if h.deletes != 1 {
		t.Fatal(h.deletes)
	}
}
func TestPollingConflictStopsHeartbeat(t *testing.T) {
	h := newHarness(t)
	heartbeat := filepath.Join(h.app.Config.StateDir, "heartbeat")
	if e := Heartbeat(heartbeat); e != nil {
		t.Fatal(e)
	}
	e := h.app.Run(context.Background())
	if e == nil || !strings.Contains(e.Error(), "conflict") {
		t.Fatal(e)
	}
	if _, e = os.Stat(heartbeat); !os.IsNotExist(e) {
		t.Fatal("heartbeat retained", e)
	}
}
func TestHealthcheck(t *testing.T) {
	dir := t.TempDir()
	if Healthcheck(dir) == nil {
		t.Fatal("missing heartbeat")
	}
	path := filepath.Join(dir, "heartbeat")
	if e := Heartbeat(path); e != nil {
		t.Fatal(e)
	}
	if e := Healthcheck(dir); e != nil {
		t.Fatal(e)
	}
	old := time.Now().Add(-3 * time.Minute)
	if e := os.Chtimes(path, old, old); e != nil {
		t.Fatal(e)
	}
	if Healthcheck(dir) == nil {
		t.Fatal("stale heartbeat")
	}
}
func TestUIEscapingAndKeyboard(t *testing.T) {
	d := readyDraft()
	d.Items[0].FoodID = ""
	d.Items[0].Name = "<Food> A&B"
	d.Items[0].Amount = 150
	d.Items[0].Creatable = &FoodSpec{Nutrition{100, 2, 3, 16, "g"}, "<Food>", "Brand"}
	d.Refresh()
	rendered := RenderDraft(d)
	if !strings.Contains(rendered, "&lt;Food&gt; A&amp;B") || !strings.Contains(rendered, "150 ккал") {
		t.Fatal(rendered)
	}
	d.NeedsReview = true
	k := DraftKeyboard(1, d)
	if k.Rows[0][0].Data != "1:r:" || len(k.Rows) != 2 {
		t.Fatal(k)
	}
	if !strings.Contains(RenderReport(WriteReport{Written: []string{"<food>"}, Failed: [][2]string{{"A&B", "invalid <token>"}}}), "invalid &lt;token&gt;") {
		t.Fatal("report HTML")
	}
}

func TestLinkPINAndTimezone(t *testing.T) {
	h := newHarness(t)
	auth := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "request_token") {
			io.WriteString(w, "oauth_token=request&oauth_token_secret=secret")
		} else {
			io.WriteString(w, "oauth_token=linked&oauth_token_secret=linked-secret")
		}
	})
	h.app.FS.AuthBase = auth.URL + "/"
	h.send(t, h.message("/link"))
	if h.app.state(42).Kind != "pin" {
		t.Fatal("PIN state")
	}
	h.send(t, h.message("1234"))
	if h.app.state(42).Kind != "tz" {
		t.Fatal("timezone state")
	}
	h.send(t, h.message("invalid/timezone"))
	if h.app.state(42).Kind != "tz" {
		t.Fatal("invalid timezone accepted")
	}
	h.send(t, h.message("Europe/Berlin"))
	u, e := h.app.Store.User(42)
	if e != nil || u.Token != "linked" || u.TZ != "Europe/Berlin" || h.app.state(42).Kind != "" {
		t.Fatal(u, e)
	}
}
func TestPollingServesUpdatesInOrderAndShutsDown(t *testing.T) {
	stopServer := make(chan struct{})
	defer close(stopServer)
	h := newHarness(t)
	done := make(chan struct{})
	var once sync.Once
	polls := 0
	server := testHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "getUpdates"):
			polls++
			if polls == 1 {
				json.NewEncoder(w).Encode(Object{"ok": true, "result": []Object{{"update_id": 1, "message": h.message("/tz").Message}, {"update_id": 2, "message": h.message("Europe/Berlin").Message}}})
			} else {
				select {
				case <-r.Context().Done():
				case <-stopServer:
				}
			}
		case strings.HasSuffix(r.URL.Path, "sendMessage"):
			var p Object
			json.NewDecoder(r.Body).Decode(&p)
			if strings.Contains(str(p["text"]), "Можно писать еду") {
				once.Do(func() { close(done) })
			}
			json.NewEncoder(w).Encode(Object{"ok": true, "result": Object{"message_id": 100}})
		default:
			io.WriteString(w, `{"ok":true,"result":true}`)
		}
	})
	h.app.TG.Base = server.URL
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result := make(chan error, 1)
	go func() { result <- h.app.Run(ctx) }()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("polling did not serve queued updates")
	}
	if e := Healthcheck(h.app.Config.StateDir); e != nil {
		t.Fatal(e)
	}
	cancel()
	select {
	case e := <-result:
		if e != nil {
			t.Fatal(e)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("shutdown hung")
	}
	u, e := h.app.Store.User(42)
	if e != nil || u.TZ != "Europe/Berlin" {
		t.Fatal(u, e)
	}
	if Healthcheck(h.app.Config.StateDir) == nil {
		t.Fatal("shutdown retained heartbeat")
	}
}
