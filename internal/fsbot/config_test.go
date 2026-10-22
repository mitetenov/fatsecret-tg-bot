package fsbot

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestConfiguration(t *testing.T) {
	t.Setenv("LLM_PROVIDER", "openrouter")
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

func TestLLMProviderConfiguration(t *testing.T) {
	for _, tc := range []struct {
		name, provider, model, effort, key, endpoint, wantError string
		env                                                     map[string]string
	}{
		{name: "default OpenRouter", provider: "openrouter", model: "google/gemini-3.5-flash-lite", key: "or-key", endpoint: "https://openrouter.ai/api/v1/chat/completions", env: map[string]string{"OPENROUTER_API_KEY": "or-key"}},
		{name: "ZAI only", provider: "zai", model: "glm-5.3-flash", effort: "low", key: "zai-key", endpoint: "https://api.z.ai/api/paas/v4/chat/completions", env: map[string]string{"LLM_PROVIDER": "zai", "ZAI_API_KEY": "zai-key"}},
		{name: "ZAI overrides", provider: "zai", model: "glm-5.3-flashx", effort: "high", key: "zai-key", endpoint: "https://api.z.ai/api/paas/v4/chat/completions", env: map[string]string{"LLM_PROVIDER": " ZAI ", "ZAI_API_KEY": "zai-key", "ZAI_MODEL": " glm-5.3-flashx ", "ZAI_REASONING_EFFORT": " HIGH ", "OPENROUTER_API_KEY": "unused", "OPENROUTER_TEXT_MODELS": "", "OPENROUTER_VISION_MODELS": ""}},
		{name: "missing ZAI key", wantError: "ZAI_API_KEY", env: map[string]string{"LLM_PROVIDER": "zai", "OPENROUTER_API_KEY": "wrong-provider-key"}},
		{name: "missing OpenRouter key", wantError: "OPENROUTER_API_KEY", env: map[string]string{"ZAI_API_KEY": "wrong-provider-key"}},
		{name: "unknown provider", wantError: "LLM_PROVIDER", env: map[string]string{"LLM_PROVIDER": "other"}},
		{name: "empty provider", wantError: "LLM_PROVIDER", env: map[string]string{"LLM_PROVIDER": ""}},
		{name: "empty ZAI model", wantError: "ZAI_MODEL", env: map[string]string{"LLM_PROVIDER": "zai", "ZAI_MODEL": " "}},
		{name: "invalid ZAI effort", wantError: "ZAI_REASONING_EFFORT", env: map[string]string{"LLM_PROVIDER": "zai", "ZAI_REASONING_EFFORT": "disabled"}},
		{name: "unused ZAI settings", provider: "openrouter", model: "google/gemini-3.5-flash-lite", key: "or-key", endpoint: "https://openrouter.ai/api/v1/chat/completions", env: map[string]string{"OPENROUTER_API_KEY": "or-key", "ZAI_MODEL": "", "ZAI_REASONING_EFFORT": "invalid"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			for _, k := range []string{"LLM_PROVIDER", "OPENROUTER_API_KEY", "OPENROUTER_TEXT_MODELS", "OPENROUTER_VISION_MODELS", "ZAI_API_KEY", "ZAI_MODEL", "ZAI_REASONING_EFFORT"} {
				t.Setenv(k, "")
				if e := os.Unsetenv(k); e != nil {
					t.Fatal(e)
				}
			}
			t.Setenv("TELEGRAM_BOT_TOKEN", "test-token")
			t.Setenv("BOT_OWNER_ID", "42")
			t.Setenv("FATSECRET_CONSUMER_KEY", "test-key")
			t.Setenv("FATSECRET_CONSUMER_SECRET", "test-secret")
			t.Setenv("FSBOT_DEFAULT_TZ", "UTC")
			for k, v := range tc.env {
				t.Setenv(k, v)
			}
			c, e := ConfigFromEnv()
			if tc.wantError != "" {
				if e == nil || !strings.Contains(e.Error(), tc.wantError) {
					t.Fatal(e)
				}
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			l := NewLLM(c)
			if l.Provider != tc.provider || l.Key != tc.key || l.Endpoint != tc.endpoint || l.ReasoningEffort != tc.effort || l.TextModels[0] != tc.model || l.VisionModels[0] != tc.model {
				t.Fatal("incorrect provider configuration")
			}
		})
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
