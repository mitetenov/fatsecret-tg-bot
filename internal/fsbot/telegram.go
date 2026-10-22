package fsbot

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

type TGUser struct {
	ID int64 `json:"id"`
}
type Chat struct {
	ID   int64  `json:"id"`
	Type string `json:"type"`
}
type Photo struct {
	FileID string `json:"file_id"`
}
type Message struct {
	ID      int64   `json:"message_id"`
	From    TGUser  `json:"from"`
	Chat    Chat    `json:"chat"`
	Text    string  `json:"text"`
	Caption string  `json:"caption"`
	Photos  []Photo `json:"photo"`
}
type Callback struct {
	ID      string   `json:"id"`
	From    TGUser   `json:"from"`
	Message *Message `json:"message"`
	Data    string   `json:"data"`
}
type Update struct {
	ID       int64     `json:"update_id"`
	Message  *Message  `json:"message"`
	Callback *Callback `json:"callback_query"`
}

func (u Update) UserID() int64 {
	if u.Message != nil {
		return u.Message.From.ID
	}
	if u.Callback != nil {
		return u.Callback.From.ID
	}
	return 0
}

type TGError struct {
	Code, RetryAfter int
	Description      string
}

func (e *TGError) Error() string { return fmt.Sprintf("Telegram %d: %s", e.Code, e.Description) }

type Telegram struct {
	Token, Base string
	HTTP        *http.Client
}

func NewTelegram(token string) *Telegram {
	return &Telegram{token, "https://api.telegram.org", &http.Client{Timeout: 65 * time.Second}}
}

// Network errors intentionally omit request URLs: Telegram embeds the bot token in them.
func (t *Telegram) Call(ctx context.Context, method string, body any, out any) error {
	b, e := json.Marshal(body)
	if e != nil {
		return e
	}
	req, e := http.NewRequestWithContext(ctx, "POST", t.Base+"/bot"+t.Token+"/"+method, bytes.NewReader(b))
	if e != nil {
		return fmt.Errorf("неверный адрес Telegram")
	}
	req.Header.Set("Content-Type", "application/json")
	r, e := t.HTTP.Do(req)
	if e != nil {
		return fmt.Errorf("Telegram: нет ответа на %s", method)
	}
	defer r.Body.Close()
	var envelope struct {
		OK          bool   `json:"ok"`
		Code        int    `json:"error_code"`
		Description string `json:"description"`
		Parameters  struct {
			RetryAfter int `json:"retry_after"`
		} `json:"parameters"`
		Result json.RawMessage `json:"result"`
	}
	e = json.NewDecoder(io.LimitReader(r.Body, 8<<20)).Decode(&envelope)
	if e != nil {
		return fmt.Errorf("Telegram: неожиданный ответ на %s", method)
	}
	if !envelope.OK {
		return &TGError{envelope.Code, envelope.Parameters.RetryAfter, envelope.Description}
	}
	if out != nil {
		return json.Unmarshal(envelope.Result, out)
	}
	return nil
}
func (t *Telegram) Send(ctx context.Context, chat int64, text string, markup any) (int64, error) {
	p := Object{"chat_id": chat, "text": text, "parse_mode": "HTML"}
	if markup != nil {
		p["reply_markup"] = markup
	}
	var m Message
	e := t.Call(ctx, "sendMessage", p, &m)
	return m.ID, e
}
func (t *Telegram) Edit(ctx context.Context, chat, id int64, text string, markup any) error {
	p := Object{"chat_id": chat, "message_id": id, "text": text, "parse_mode": "HTML"}
	if markup != nil {
		p["reply_markup"] = markup
	}
	e := t.Call(ctx, "editMessageText", p, nil)
	if te, ok := e.(*TGError); ok && strings.Contains(te.Description, "message is not modified") {
		return nil
	}
	return e
}
func (t *Telegram) Markup(ctx context.Context, chat, id int64, k Keyboard) error {
	return t.Call(ctx, "editMessageReplyMarkup", Object{"chat_id": chat, "message_id": id, "reply_markup": k}, nil)
}
func (t *Telegram) Answer(ctx context.Context, c *Callback, text string) error {
	return t.Call(ctx, "answerCallbackQuery", Object{"callback_query_id": c.ID, "text": text, "show_alert": text != ""}, nil)
}
func (t *Telegram) Download(ctx context.Context, id string) ([]byte, error) {
	var file struct {
		Path string `json:"file_path"`
	}
	if e := t.Call(ctx, "getFile", Object{"file_id": id}, &file); e != nil {
		return nil, e
	}
	if file.Path == "" || strings.Contains(file.Path, "..") {
		return nil, fmt.Errorf("Telegram не вернул путь к фото")
	}
	req, e := http.NewRequestWithContext(ctx, "GET", t.Base+"/file/bot"+t.Token+"/"+file.Path, nil)
	if e != nil {
		return nil, fmt.Errorf("неверный путь к фото")
	}
	r, e := t.HTTP.Do(req)
	if e != nil {
		return nil, fmt.Errorf("не удалось скачать фото Telegram")
	}
	defer r.Body.Close()
	if r.StatusCode != 200 {
		return nil, fmt.Errorf("не удалось скачать фото: HTTP %d", r.StatusCode)
	}
	b, e := io.ReadAll(io.LimitReader(r.Body, 20<<20+1))
	if e != nil {
		return nil, fmt.Errorf("не удалось прочитать фото")
	}
	if len(b) > 20<<20 {
		return nil, fmt.Errorf("фото больше 20 МБ")
	}
	return b, nil
}
