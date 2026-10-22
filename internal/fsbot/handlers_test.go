package fsbot

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

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
