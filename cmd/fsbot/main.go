package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/mitetenov/fatsecret-tg-bot/internal/fsbot"
)

var version = "dev"

func run() error {
	dir := os.Getenv("FSBOT_STATE_DIR")
	if dir == "" {
		dir = "/data"
	}
	if len(os.Args) > 1 {
		switch os.Args[1] {
		case "version":
			fmt.Println(version)
			return nil
		case "healthcheck":
			return fsbot.Healthcheck(dir)
		case "check":
			if e := fsbot.Bootstrap(dir); e != nil {
				return e
			}
			store, e := fsbot.OpenStore(filepath.Join(dir, "fsbot.sqlite3"))
			if e != nil {
				return e
			}
			defer store.Close()
			if e = fsbot.Heartbeat(filepath.Join(dir, "heartbeat")); e != nil {
				return e
			}
			fmt.Printf("SQLite, права на каталог и heartbeat: OK (uid=%d gid=%d)\n", os.Getuid(), os.Getgid())
			return nil
		default:
			return fmt.Errorf("неизвестная команда: %s", os.Args[1])
		}
	}
	c, e := fsbot.ConfigFromEnv()
	if e != nil {
		return e
	}
	secrets := []string{c.TelegramToken, c.ConsumerKey, c.ConsumerSecret, c.OpenRouterKey, c.ZAIKey}
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{ReplaceAttr: func(groups []string, a slog.Attr) slog.Attr {
		if a.Value.Kind() == slog.KindString || a.Key == "error" {
			v := fmt.Sprint(a.Value.Any())
			for _, secret := range secrets {
				if secret != "" {
					v = strings.ReplaceAll(v, secret, "[REDACTED]")
				}
			}
			a.Value = slog.StringValue(v)
		}
		return a
	}})))
	if e = fsbot.Bootstrap(c.StateDir); e != nil {
		return e
	}
	s, e := fsbot.OpenStore(c.DBPath())
	if e != nil {
		return e
	}
	defer s.Close()
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	slog.Info("fsbot", "version", version,
		"llm_provider", c.LLMProvider,
		"text_models", strings.Join(c.TextModels, ","),
		"vision_models", strings.Join(c.VisionModels, ","))
	return fsbot.NewApp(c, s).Run(ctx)
}
func main() {
	if e := run(); e != nil {
		slog.Error("бот остановлен", "error", e)
		os.Exit(1)
	}
}
