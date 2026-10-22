package fsbot

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"sync"
	"time"
)

var PublicCommands = []Object{{"command": "start", "description": "что умеет бот"}, {"command": "help", "description": "как пользоваться"}, {"command": "link", "description": "привязать аккаунт FatSecret"}, {"command": "tz", "description": "часовой пояс"}, {"command": "undo", "description": "отменить последнюю запись"}, {"command": "attribution", "description": "об источнике данных"}}

func Heartbeat(path string) error {
	return os.WriteFile(path, []byte(time.Now().UTC().Format(time.RFC3339)), 0600)
}
func Healthcheck(dir string) error {
	f, e := os.Stat(filepath.Join(dir, "heartbeat"))
	if e != nil {
		return fmt.Errorf("heartbeat отсутствует")
	}
	age := time.Since(f.ModTime())
	if age > 2*time.Minute || age < -time.Minute {
		return fmt.Errorf("heartbeat устарел")
	}
	return nil
}
func waitContext(ctx context.Context, d time.Duration) bool {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}
func (a *App) Run(ctx context.Context) error {
	heartbeat := filepath.Join(a.Config.StateDir, "heartbeat")
	if e := os.Remove(heartbeat); e != nil && !os.IsNotExist(e) {
		return e
	}
	if e := a.TG.Call(ctx, "setMyCommands", Object{"commands": PublicCommands, "scope": Object{"type": "default"}}, nil); e != nil {
		return e
	}
	owner := append(append([]Object{}, PublicCommands...), Object{"command": "allow", "description": "открыть доступ"})
	if e := a.TG.Call(ctx, "setMyCommands", Object{"commands": owner, "scope": Object{"type": "chat", "chat_id": a.Config.OwnerID}}, nil); e != nil {
		return e
	}
	workerCtx, cancel := context.WithCancel(ctx)
	var wg sync.WaitGroup
	queues := make([]chan Update, 8)
	for p := range queues {
		queues[p] = make(chan Update, 16)
		wg.Go(func() {
			for {
				select {
				case <-workerCtx.Done():
					return
				case u := <-queues[p]:
					if e := a.Handle(workerCtx, u); e != nil {
						if workerCtx.Err() != nil {
							return
						}
						slog.Error("ошибка обработки сообщения", "update_id", u.ID, "error", e)
						m := u.Message
						if m == nil && u.Callback != nil {
							m = u.Callback.Message
						}
						if m != nil && m.Chat.Type == "private" {
							_, sendErr := a.TG.Send(workerCtx, m.Chat.ID, "Что-то сломалось на моей стороне — я записал это в лог. Попробуй ещё раз; если повторится, скажи владельцу бота.", nil)
							if sendErr != nil {
								slog.Warn("не удалось сообщить об ошибке", "error", sendErr)
							}
						}
					}
				}
			}
		})
	}
	defer func() { cancel(); wg.Wait(); os.Remove(heartbeat) }()
	slog.Info("бот запущен", "decoder", "gozxing", "workers", len(queues))
	var offset int64
	backoff := time.Second
	for ctx.Err() == nil {
		var updates []Update
		e := a.TG.Call(ctx, "getUpdates", Object{"offset": offset, "timeout": 30, "allowed_updates": []string{"message", "callback_query"}}, &updates)
		if e != nil {
			if ctx.Err() != nil {
				break
			}
			var te *TGError
			if errors.As(e, &te) {
				switch te.Code {
				case 409:
					return fmt.Errorf("второй экземпляр бота на том же токене: polling conflict")
				case 401, 403:
					return fmt.Errorf("Telegram отклонил токен бота")
				}
				if te.RetryAfter > 0 {
					backoff = time.Duration(te.RetryAfter) * time.Second
				}
			}
			slog.Warn("polling временно недоступен", "error", e)
			if !waitContext(ctx, backoff) {
				break
			}
			backoff = min(backoff*2, 30*time.Second)
			continue
		}
		backoff = time.Second
		if e = Heartbeat(heartbeat); e != nil {
			return e
		}
		for _, u := range updates {
			if u.ID < offset {
				continue
			}
			select {
			case queues[uint64(u.UserID())%uint64(len(queues))] <- u:
				offset = u.ID + 1
			case <-ctx.Done():
				return nil
			}
		}
	}
	return nil
}
