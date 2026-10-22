package fsbot

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestConfiguration(t *testing.T) {
	for _, k := range []string{"TELEGRAM_BOT_TOKEN", "BOT_OWNER_ID", "FATSECRET_CONSUMER_KEY", "FATSECRET_CONSUMER_SECRET", "OPENROUTER_API_KEY"} {
		t.Setenv(k, "")
	}
	if _, e := ConfigFromEnv(); e == nil || !strings.Contains(e.Error(), "TELEGRAM_BOT_TOKEN") {
		t.Fatal(e)
	}
	t.Setenv("TELEGRAM_BOT_TOKEN", "test-token")
	t.Setenv("BOT_OWNER_ID", "42")
	t.Setenv("FATSECRET_CONSUMER_KEY", "test-key")
	t.Setenv("FATSECRET_CONSUMER_SECRET", "test-secret")
	t.Setenv("OPENROUTER_API_KEY", "llm-key")
	t.Setenv("FSBOT_DEFAULT_TZ", "Asia/Tbilisi")
	t.Setenv("OPENROUTER_TEXT_MODELS", "first, second, ")
	t.Setenv("OPENROUTER_VISION_MODELS", "vision")
	c, e := ConfigFromEnv()
	if e != nil || c.OwnerID != 42 || len(c.TextModels) != 2 {
		t.Fatal(c.OwnerID, e)
	}
	t.Setenv("BOT_OWNER_ID", "bad")
	if _, e := ConfigFromEnv(); e == nil {
		t.Fatal("bad owner")
	}
	t.Setenv("BOT_OWNER_ID", "42")
	t.Setenv("FSBOT_DEFAULT_TZ", "Local")
	if _, e := ConfigFromEnv(); e == nil {
		t.Fatal("implicit timezone")
	}
	t.Setenv("FSBOT_DEFAULT_TZ", "UTC")
	t.Setenv("OPENROUTER_TEXT_MODELS", "")
	if _, e := ConfigFromEnv(); e == nil {
		t.Fatal("empty models")
	}
}
func TestDotenvDoesNotOverrideEnvironment(t *testing.T) {
	t.Setenv("FSBOT_TEST_EXISTING", "existing")
	path := filepath.Join(t.TempDir(), "config.env")
	if e := os.WriteFile(path, []byte("# comment\nFSBOT_TEST_EXISTING=replacement\n"), 0600); e != nil {
		t.Fatal(e)
	}
	if e := LoadDotenv(path); e != nil {
		t.Fatal(e)
	}
	if os.Getenv("FSBOT_TEST_EXISTING") != "existing" {
		t.Fatal("env overwritten")
	}
}
