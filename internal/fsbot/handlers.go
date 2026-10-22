package fsbot

import (
	"context"
	"errors"
	"fmt"
	"html"
	"log/slog"
	"regexp"
	"strconv"
	"strings"
	"sync"
)

type dialogue struct {
	Kind, Token, Secret, Barcode, ServingID string
	DraftID                                 int64
	Index                                   int
}
type App struct {
	Config Config
	Store  *Store
	FS     *FatSecret
	LLM    *LLM
	OFF    *FoodFacts
	TG     *Telegram
	mu     sync.Mutex
	states map[int64]dialogue
}

func NewApp(c Config, s *Store) *App {
	return &App{Config: c, Store: s, FS: NewFatSecret(c.ConsumerKey, c.ConsumerSecret), LLM: NewLLM(c), OFF: NewFoodFacts(), TG: NewTelegram(c.TelegramToken), states: map[int64]dialogue{}}
}
func (a *App) state(id int64) dialogue { a.mu.Lock(); defer a.mu.Unlock(); return a.states[id] }
func (a *App) setState(id int64, d dialogue) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if d.Kind == "" {
		delete(a.states, id)
	} else {
		a.states[id] = d
	}
}
func (a *App) Handle(ctx context.Context, u Update) error {
	if u.Callback != nil {
		c := u.Callback
		if c.Message == nil || c.Message.Chat.Type != "private" {
			return nil
		}
		return a.callback(ctx, c)
	}
	m := u.Message
	if m == nil || m.Chat.Type != "private" {
		return nil
	}
	return a.message(ctx, m)
}
func (a *App) gate(ctx context.Context, id, chat int64) (*User, error) {
	u, e := a.Store.EnsureUser(id)
	if e != nil {
		return nil, e
	}
	if id != a.Config.OwnerID && !u.Allowed {
		_, e = a.TG.Send(ctx, chat, fmt.Sprintf("Бот личный, доступ по приглашению.\nТвой id: <code>%d</code>", id), nil)
		return nil, e
	}
	return u, nil
}
func (a *App) linked(ctx context.Context, u *User, chat int64) (bool, error) {
	if u.Linked() {
		return true, nil
	}
	_, e := a.TG.Send(ctx, chat, "Сначала привяжи аккаунт FatSecret: /link", nil)
	return false, e
}
func (a *App) tz(u *User) string { return first(u.TZ, a.Config.DefaultTZ) }

var barcodePattern = regexp.MustCompile(`^[0-9]{8,14}$`)
var bareAmountPattern = regexp.MustCompile(`(?i)^[0-9]{1,4}([.,][0-9]+)?\s*(г|гр|g|мл|ml)?$`)

func (a *App) message(ctx context.Context, m *Message) error {
	id, chat := m.From.ID, m.Chat.ID
	text := strings.TrimSpace(m.Text)
	cmd, arg, _ := strings.Cut(text, " ")
	cmd = strings.Split(cmd, "@")[0]
	if cmd == "/attribution" {
		_, e := a.TG.Send(ctx, chat, Attribution, nil)
		return e
	}
	u, e := a.gate(ctx, id, chat)
	if e != nil || u == nil {
		return e
	}
	send := func(s string) error { _, e := a.TG.Send(ctx, chat, s, nil); return e }
	switch cmd {
	case "/start", "/help":
		return func() error { _, e := a.TG.Send(ctx, chat, HelpText, Object{"remove_keyboard": true}); return e }()
	case "/allow":
		if id != a.Config.OwnerID {
			return nil
		}
		invited, e := strconv.ParseInt(strings.TrimSpace(arg), 10, 64)
		if e != nil || invited <= 0 {
			return send("Использование: <code>/allow 123456789</code>")
		}
		if e = a.Store.Allow(invited); e != nil {
			return e
		}
		return send(fmt.Sprintf("Доступ открыт для <code>%d</code>.", invited))
	case "/link":
		token, secret, url, e := a.FS.RequestToken(ctx)
		if e != nil {
			return send("FatSecret не выдал токен: " + html.EscapeString(e.Error()))
		}
		a.setState(id, dialogue{Kind: "pin", Token: token, Secret: secret})
		return send("1. Открой ссылку и разреши доступ:\n" + html.EscapeString(url) + "\n\n2. FatSecret покажет PIN — пришли его сюда ответным сообщением.")
	case "/tz":
		return a.setTZ(ctx, m, strings.TrimSpace(arg))
	case "/undo":
		ok, e := a.linked(ctx, u, chat)
		if e != nil || !ok {
			return e
		}
		return a.undo(ctx, m, u)
	}
	st := a.state(id)
	switch st.Kind {
	case "pin":
		token, secret, e := a.FS.AccessToken(ctx, st.Token, st.Secret, text)
		if e != nil {
			return send(html.EscapeString(e.Error()) + "\nПришли PIN ещё раз или начни заново: /link")
		}
		if e = a.Store.SaveLink(id, token, secret); e != nil {
			return e
		}
		a.setState(id, dialogue{Kind: "tz"})
		return send("Аккаунт привязан.\n\nТеперь часовой пояс — пришли название вида <code>" + html.EscapeString(a.Config.DefaultTZ) + "</code> или напиши «по умолчанию».")
	case "tz":
		return a.setTZ(ctx, m, text)
	case "amount", "portions":
		n, ok := PositiveNumber(text)
		if !ok {
			return send("Нужно положительное число, например <code>150</code>.")
		}
		a.setState(id, dialogue{})
		return a.editAmount(ctx, m, st, n)
	}
	ok, e := a.linked(ctx, u, chat)
	if e != nil || !ok {
		return e
	}
	if len(m.Photos) > 0 {
		a.setState(id, dialogue{})
		return a.photo(ctx, m, u, st.Barcode)
	}
	if text == "" {
		return nil
	}
	a.setState(id, dialogue{})
	if barcodePattern.MatchString(text) {
		return a.barcode(ctx, m, u, text)
	}
	if bareAmountPattern.MatchString(text) {
		draftID, d, e := a.Store.LastDraft(id)
		if e != nil {
			return e
		}
		if d != nil && len(d.Items) == 1 {
			n, ok := PositiveNumber(text)
			if !ok {
				return send("Количество должно быть положительным.")
			}
			return a.editAmount(ctx, m, dialogue{DraftID: draftID, Index: 0, Kind: "amount"}, n)
		}
	}
	note, e := a.TG.Send(ctx, chat, "Разбираю…", nil)
	if e != nil {
		return e
	}
	r, e := a.LLM.Text(ctx, text)
	if e != nil {
		return a.TG.Edit(ctx, chat, note, LLMFailure(e, false), nil)
	}
	return a.present(ctx, chat, note, u, r, "")
}
func (a *App) setTZ(ctx context.Context, m *Message, tz string) error {
	if tz == "" {
		a.setState(m.From.ID, dialogue{Kind: "tz"})
		_, e := a.TG.Send(ctx, m.Chat.ID, "Пришли часовой пояс, например <code>"+html.EscapeString(a.Config.DefaultTZ)+"</code>.", nil)
		return e
	}
	if strings.EqualFold(tz, "default") || strings.EqualFold(tz, "по умолчанию") {
		tz = a.Config.DefaultTZ
	}
	if _, e := LoadTimezone(tz); e != nil {
		_, e = a.TG.Send(ctx, m.Chat.ID, "Не знаю такого пояса. Нужно имя из базы IANA, например <code>Asia/Tbilisi</code> или <code>Europe/Berlin</code>.", nil)
		return e
	}
	if e := a.Store.SetTZ(m.From.ID, tz); e != nil {
		return e
	}
	a.setState(m.From.ID, dialogue{})
	_, e := a.TG.Send(ctx, m.Chat.ID, "Часовой пояс: <b>"+html.EscapeString(tz)+"</b>. Можно писать еду.", nil)
	return e
}
func (a *App) undo(ctx context.Context, m *Message, u *User) error {
	id, ids, e := a.Store.ClaimBatch(u.ID)
	if e != nil {
		return e
	}
	if id == 0 {
		_, e = a.TG.Send(ctx, m.Chat.ID, "Нечего отменять или последняя запись сейчас обрабатывается.", nil)
		return e
	}
	defer func() {
		if e := a.Store.ReleaseBatch(id, u.ID); e != nil {
			slog.Error("не удалось освободить отмену", "error", e)
		}
	}()
	removed := 0
	failed := []string{}
	for _, entry := range ids {
		e := a.FS.DeleteEntry(ctx, u, entry)
		if e != nil {
			failed = append(failed, e.Error())
			continue
		}
		if e = a.Store.RecordUndo(id, u.ID, entry); e != nil {
			return e
		}
		removed++
	}
	answer := fmt.Sprintf("Удалил записей: %d.", removed)
	if len(failed) > 0 {
		answer += "\nНе удалось: " + html.EscapeString(strings.Join(failed[:min(3, len(failed))], "; "))
	}
	_, e = a.TG.Send(ctx, m.Chat.ID, answer, nil)
	return e
}
func (a *App) show(ctx context.Context, chat, note int64, u *User, d *Draft) error {
	d.CardMessageID = note
	id, e := a.Store.SaveDraft(u.ID, d)
	if e != nil {
		return e
	}
	return a.TG.Edit(ctx, chat, note, RenderDraft(d), DraftKeyboard(id, d))
}
func (a *App) present(ctx context.Context, chat, note int64, u *User, r Recognition, code string) error {
	recent, e := a.FS.Recent(ctx, u)
	if e != nil {
		slog.Info("недавние продукты недоступны")
	}
	d, e := BuildDraft(ctx, a.FS, r, a.tz(u), recent, code)
	if e != nil {
		return e
	}
	slog.Info("распознан черновик", "items", len(d.Items), "kind", r.Kind)
	return a.show(ctx, chat, note, u, d)
}
func (a *App) lookupFood(ctx context.Context, u *User, code string) (string, error) {
	id, e := a.Store.BoundFood(u.ID, code)
	if e != nil || id != "" {
		return id, e
	}
	return a.FS.Barcode(ctx, code)
}
func (a *App) product(ctx context.Context, code string) *Product {
	code = CanonicalGTIN(code, "")
	if code == "" {
		return nil
	}
	p := a.OFF.Lookup(ctx, code)
	if p == nil {
		p = a.LLM.Lookup(ctx, code)
	}
	if p != nil && !Readable(p.Name) && !a.LLM.Translate(ctx, p) {
		return nil
	}
	return p
}
func (a *App) showFood(ctx context.Context, chat, note int64, u *User, id string) error {
	d, e := DraftFromFood(ctx, a.FS, id, a.tz(u))
	if e != nil {
		return e
	}
	return a.show(ctx, chat, note, u, d)
}
func (a *App) barcode(ctx context.Context, m *Message, u *User, code string) error {
	code = CanonicalGTIN(code, "")
	if code == "" {
		_, e := a.TG.Send(ctx, m.Chat.ID, "Штрих-код неверной длины или с неправильной контрольной цифрой.", nil)
		return e
	}
	note, e := a.TG.Send(ctx, m.Chat.ID, "Ищу по штрих-коду…", nil)
	if e != nil {
		return e
	}
	id, e := a.lookupFood(ctx, u, code)
	if e != nil {
		return e
	}
	if id != "" {
		return a.showFood(ctx, m.Chat.ID, note, u, id)
	}
	if p := a.product(ctx, code); p != nil {
		d, e := DraftFromProduct(ctx, a.FS, *p, a.tz(u), code)
		if e != nil {
			return e
		}
		return a.show(ctx, m.Chat.ID, note, u, d)
	}
	a.setState(u.ID, dialogue{Kind: "label", Barcode: code})
	return a.TG.Edit(ctx, m.Chat.ID, note, BarcodeUnknown, nil)
}
func (a *App) photo(ctx context.Context, m *Message, u *User, code string) error {
	note, e := a.TG.Send(ctx, m.Chat.ID, "Смотрю фото…", nil)
	if e != nil {
		return e
	}
	image, e := a.TG.Download(ctx, m.Photos[len(m.Photos)-1].FileID)
	if e != nil {
		return e
	}
	if scanned := DecodeBarcode(image); scanned != "" {
		id, e := a.lookupFood(ctx, u, scanned)
		if e != nil {
			return e
		}
		if id != "" {
			return a.showFood(ctx, m.Chat.ID, note, u, id)
		}
		if p := a.product(ctx, scanned); p != nil {
			d, e := DraftFromProduct(ctx, a.FS, *p, a.tz(u), scanned)
			if e != nil {
				return e
			}
			return a.show(ctx, m.Chat.ID, note, u, d)
		}
		code = scanned
	}
	r, e := a.LLM.Photo(ctx, image, m.Caption, code)
	if e != nil {
		return a.TG.Edit(ctx, m.Chat.ID, note, LLMFailure(e, true), nil)
	}
	return a.present(ctx, m.Chat.ID, note, u, r, code)
}
func (a *App) saveCard(ctx context.Context, chat, id, user int64, d *Draft) error {
	d.ReviewPrompted = false
	d.Refresh()
	if e := a.Store.UpdateDraft(id, user, d); e != nil {
		return e
	}
	if d.CardMessageID != 0 {
		if e := a.TG.Edit(ctx, chat, d.CardMessageID, RenderDraft(d), DraftKeyboard(id, d)); e == nil {
			return nil
		}
	}
	message, e := a.TG.Send(ctx, chat, RenderDraft(d), DraftKeyboard(id, d))
	if e != nil {
		return e
	}
	d.CardMessageID = message
	return a.Store.UpdateDraft(id, user, d)
}
func (a *App) editAmount(ctx context.Context, m *Message, st dialogue, n float64) error {
	d, e := a.Store.ClaimDraft(st.DraftID, m.From.ID)
	if e != nil {
		return e
	}
	if d == nil {
		_, e = a.TG.Send(ctx, m.Chat.ID, "Черновик сейчас обрабатывается или уже закрыт.", nil)
		return e
	}
	defer a.releaseDraft(st.DraftID, m.From.ID)
	if st.Index < 0 || st.Index >= len(d.Items) {
		return fmt.Errorf("неверный номер позиции")
	}
	i := d.Items[st.Index]
	if i.Locked() {
		_, e = a.TG.Send(ctx, m.Chat.ID, "Эту позицию нельзя менять сейчас.", nil)
		return e
	}
	i.Amount = n
	if st.Kind == "portions" {
		allowed := false
		for _, s := range i.Available {
			allowed = allowed || s.ID == st.ServingID
		}
		if !allowed {
			return fmt.Errorf("порция уже неактуальна")
		}
		i.ManualServingID = st.ServingID
		i.Unit = "piece"
	}
	if e = ApplyCandidate(ctx, a.FS, i, i.Chosen); e != nil {
		return e
	}
	return a.saveCard(ctx, m.Chat.ID, st.DraftID, m.From.ID, d)
}
func (a *App) releaseDraft(id, user int64) {
	if e := a.Store.ReleaseDraft(id, user); e != nil {
		slog.Error("не удалось освободить черновик", "error", e)
	}
}
func (a *App) callback(ctx context.Context, c *Callback) error {
	id, action, arg, e := ParseCallback(c.Data)
	if e != nil {
		return a.TG.Answer(ctx, c, "Неизвестная кнопка")
	}
	u, e := a.Store.User(c.From.ID)
	if e != nil {
		return e
	}
	if u == nil || u.ID != a.Config.OwnerID && !u.Allowed {
		return a.TG.Answer(ctx, c, "Нет доступа")
	}
	d, e := a.Store.ClaimDraft(id, u.ID)
	if e != nil {
		return e
	}
	if d == nil {
		return a.TG.Answer(ctx, c, "Черновик уже неактуален или обрабатывается")
	}
	defer a.releaseDraft(id, u.ID)
	return a.handleCallback(ctx, c, u, id, action, arg, d)
}
func (a *App) handleCallback(ctx context.Context, c *Callback, u *User, id int64, action, arg string, d *Draft) error {
	chat, card := c.Message.Chat.ID, c.Message.ID
	answer := func(s string) error { return a.TG.Answer(ctx, c, s) }
	show := func(k Keyboard) error { return a.TG.Edit(ctx, chat, card, RenderDraft(d), k) }
	persist := func() error { return a.Store.UpdateDraft(id, u.ID, d) }
	switch action {
	case "x":
		written, uncertain := 0, false
		for _, i := range d.Items {
			if i.Status == "written" {
				written++
			}
			uncertain = uncertain || i.Uncertain()
		}
		if e := a.Store.DeleteDraft(id, u.ID); e != nil {
			return e
		}
		notice := "Отменил, в дневник ничего не пошло."
		if written > 0 || uncertain {
			notice = fmt.Sprintf("Черновик закрыт. Уже записано позиций: %d.", written)
			if written > 0 {
				notice += " Их можно удалить командой /undo."
			}
			if uncertain {
				notice += " Исход ещё одного запроса неизвестен — проверь FatSecret."
			}
		}
		if e := a.TG.Edit(ctx, chat, card, notice, Keyboard{Rows: [][]Button{}}); e != nil {
			return e
		}
		return answer("")
	case "r":
		d.ReviewPrompted = true
		if e := persist(); e != nil {
			return e
		}
		if e := a.TG.Markup(ctx, chat, card, ReviewKeyboard(id)); e != nil {
			return e
		}
		return answer("Проверь продукт, количество и КБЖУ. Повторное нажатие выполнит запись.")
	case "w":
		if d.NeedsReview && !d.ReviewPrompted {
			d.ReviewPrompted = true
			if e := persist(); e != nil {
				return e
			}
			if e := a.TG.Markup(ctx, chat, card, ReviewKeyboard(id)); e != nil {
				return e
			}
			return answer("Нужна дополнительная проверка")
		}
		if !u.Linked() {
			return answer("Сначала /link")
		}
		if e := answer(""); e != nil {
			return e
		}
		r, e := WriteDraft(ctx, a.FS, d, u, persist, func(entry string) error { return a.Store.RecordWrite(id, u.ID, d, entry) })
		if e != nil {
			return e
		}
		if r.TokenInvalid {
			if e = a.Store.InvalidateLink(u.ID); e != nil {
				return e
			}
		}
		if len(r.Failed) == 0 {
			if e = a.Store.DeleteDraft(id, u.ID); e != nil {
				return e
			}
			return a.TG.Edit(ctx, chat, card, RenderReport(r), Keyboard{Rows: [][]Button{}})
		}
		return a.TG.Edit(ctx, chat, card, RenderReport(r), DraftKeyboard(id, d))
	case "e":
		d.ReviewPrompted = false
		if e := persist(); e != nil {
			return e
		}
		if e := show(EditKeyboard(id, d)); e != nil {
			return e
		}
	case "b":
		if e := show(DraftKeyboard(id, d)); e != nil {
			return e
		}
	case "m", "d":
		if arg == "" {
			var k Keyboard
			if action == "m" {
				k = MealKeyboard(id)
			} else {
				k = DateKeyboard(id)
			}
			if e := a.TG.Markup(ctx, chat, card, k); e != nil {
				return e
			}
			break
		}
		if !d.CanShift() {
			return answer("Часть позиций уже записана; дату и приём пищи теперь нельзя менять")
		}
		if action == "m" {
			if MealRU[arg] == "" {
				return answer("Неизвестный приём пищи")
			}
			d.Meal = arg
		} else {
			if e := ShiftDay(d, arg, a.tz(u)); e != nil {
				return answer("Неизвестная дата")
			}
		}
		if e := persist(); e != nil {
			return e
		}
		if e := show(DraftKeyboard(id, d)); e != nil {
			return e
		}
	case "i", "c", "g", "s", "n":
		parts := strings.SplitN(arg, ".", 2)
		index, e := strconv.Atoi(parts[0])
		if e != nil || index < 0 || index >= len(d.Items) {
			return answer("Позиция уже неактуальна")
		}
		i := d.Items[index]
		if i.Locked() {
			return answer("Эту позицию нельзя менять сейчас")
		}
		switch action {
		case "i":
			if e = show(ItemKeyboard(id, index, i)); e != nil {
				return e
			}
		case "g":
			a.setState(u.ID, dialogue{Kind: "amount", DraftID: id, Index: index})
			if _, e = a.TG.Send(ctx, chat, "Пришли количество числом — граммы или штуки.", nil); e != nil {
				return e
			}
		case "s":
			if len(parts) != 2 || !i.NeedsPortion {
				return answer("Порция уже неактуальна")
			}
			valid := false
			for _, s := range i.Available {
				valid = valid || s.ID == parts[1]
			}
			if !valid {
				return answer("Порция уже неактуальна")
			}
			a.setState(u.ID, dialogue{Kind: "portions", DraftID: id, Index: index, ServingID: parts[1]})
			if _, e = a.TG.Send(ctx, chat, "Сколько таких порций съел? Пришли число, например 1,5.", nil); e != nil {
				return e
			}
		case "c":
			if len(parts) != 2 {
				return answer("Неизвестный кандидат")
			}
			position, e := strconv.Atoi(parts[1])
			if e != nil || position < 0 || position >= len(i.Candidates) {
				return answer("Неизвестный кандидат")
			}
			i.ManualServingID = ""
			if e = ApplyCandidate(ctx, a.FS, i, position); e != nil {
				return e
			}
			if e = a.saveCard(ctx, chat, id, u.ID, d); e != nil {
				return e
			}
		case "n":
			if !u.Linked() {
				return answer("Сначала /link")
			}
			if i.Creatable == nil || i.Status != "pending" {
				return answer("Продукт уже создаётся или создан")
			}
			if e = answer("Создаю продукт…"); e != nil {
				return e
			}
			i.Status = "creating"
			if e = persist(); e != nil {
				return e
			}
			spec := *i.Creatable
			foodID, e := a.FS.CreateFood(ctx, u, spec)
			if e != nil {
				var fe *FSError
				if errors.As(e, &fe) {
					i.Status = "pending"
					i.Error = fe.Message
				} else {
					i.Status = "create_unknown"
					i.Error = "Исход создания неизвестен — проверь FatSecret"
				}
				if e = persist(); e != nil {
					return e
				}
				return show(DraftKeyboard(id, d))
			}
			i.Status = "pending"
			i.OwnFoodID = foodID
			i.Creatable = nil
			i.Candidates = []Candidate{{FoodID: foodID, Title: spec.Name, Description: "свой"}}
			if e = persist(); e != nil {
				return e
			}
			if d.Barcode != "" {
				if e = a.Store.BindBarcode(u.ID, d.Barcode, foodID); e != nil {
					return e
				}
			}
			if e = ApplyCandidate(ctx, a.FS, i, 0); e != nil {
				i.Error = "Продукт создан; не удалось загрузить порции. Выбери его ещё раз."
			}
			return a.saveCard(ctx, chat, id, u.ID, d)
		}
	}
	return answer("")
}
