package fsbot

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

type Config struct {
	TelegramToken, ConsumerKey, ConsumerSecret, OpenRouterKey, StateDir, DefaultTZ string
	OwnerID                                                                        int64
	TextModels, VisionModels                                                       []string
}

func envDefault(k, v string) string {
	if x, ok := os.LookupEnv(k); ok {
		return x
	}
	return v
}
func splitModels(s string) []string {
	var out []string
	for _, v := range strings.Split(s, ",") {
		if v = strings.TrimSpace(v); v != "" {
			out = append(out, v)
		}
	}
	return out
}

// Optional .env is explicit: production reads only the environment, as before.
func LoadDotenv(path string) error {
	f, e := os.Open(path)
	if e != nil {
		return e
	}
	defer f.Close()
	s := bufio.NewScanner(f)
	for s.Scan() {
		line := strings.TrimSpace(s.Text())
		if strings.HasPrefix(line, "#") {
			continue
		}
		k, v, ok := strings.Cut(line, "=")
		if ok {
			k = strings.TrimSpace(k)
			if _, set := os.LookupEnv(k); !set {
				if e = os.Setenv(k, strings.Trim(strings.TrimSpace(v), "\"'")); e != nil {
					return e
				}
			}
		}
	}
	return s.Err()
}
func ConfigFromEnv() (Config, error) {
	c := Config{TelegramToken: os.Getenv("TELEGRAM_BOT_TOKEN"), ConsumerKey: os.Getenv("FATSECRET_CONSUMER_KEY"), ConsumerSecret: os.Getenv("FATSECRET_CONSUMER_SECRET"), OpenRouterKey: os.Getenv("OPENROUTER_API_KEY"), StateDir: envDefault("FSBOT_STATE_DIR", "/data"), DefaultTZ: envDefault("FSBOT_DEFAULT_TZ", "Asia/Tbilisi"), TextModels: splitModels(envDefault("OPENROUTER_TEXT_MODELS", "google/gemini-3.5-flash-lite,openrouter/free")), VisionModels: splitModels(envDefault("OPENROUTER_VISION_MODELS", "google/gemini-3.5-flash-lite,openrouter/free"))}
	var missing []string
	for _, k := range []string{"TELEGRAM_BOT_TOKEN", "BOT_OWNER_ID", "FATSECRET_CONSUMER_KEY", "FATSECRET_CONSUMER_SECRET", "OPENROUTER_API_KEY"} {
		if os.Getenv(k) == "" {
			missing = append(missing, k)
		}
	}
	if len(missing) > 0 {
		return c, fmt.Errorf("не заданы переменные окружения: %s", strings.Join(missing, ", "))
	}
	var e error
	c.OwnerID, e = strconv.ParseInt(os.Getenv("BOT_OWNER_ID"), 10, 64)
	if e != nil || c.OwnerID <= 0 {
		return c, fmt.Errorf("BOT_OWNER_ID должен быть положительным числом")
	}
	if _, e = LoadTimezone(c.DefaultTZ); e != nil {
		return c, fmt.Errorf("FSBOT_DEFAULT_TZ: %w", e)
	}
	if len(c.TextModels) == 0 || len(c.VisionModels) == 0 {
		return c, fmt.Errorf("списки моделей не должны быть пустыми")
	}
	return c, nil
}
func (c Config) DBPath() string { return filepath.Join(c.StateDir, "fsbot.sqlite3") }
