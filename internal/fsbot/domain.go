package fsbot

import (
	"bytes"
	"fmt"
	"image"
	_ "image/jpeg"
	_ "image/png"
	"math"
	"regexp"
	"strings"
	"time"
	_ "time/tzdata"
	"unicode"

	"github.com/makiuchi-d/gozxing"
	"github.com/makiuchi-d/gozxing/oned"
)

func ValidGTIN(s string) bool {
	if len(s) != 8 && len(s) != 12 && len(s) != 13 && len(s) != 14 {
		return false
	}
	sum := 0
	weight := 3
	for i := len(s) - 2; i >= 0; i-- {
		if s[i] < '0' || s[i] > '9' {
			return false
		}
		sum += int(s[i]-'0') * weight
		weight = 4 - weight
	}
	return s[len(s)-1] >= '0' && s[len(s)-1] <= '9' && (10-sum%10)%10 == int(s[len(s)-1]-'0')
}
func ExpandUPCE(s string) string {
	if len(s) != 8 || !regexp.MustCompile(`^[01][0-9]{7}$`).MatchString(s) {
		return ""
	}
	b := s[1:7]
	var d string
	switch b[5] {
	case '0', '1', '2':
		d = s[:1] + b[:2] + b[5:] + "0000" + b[2:5]
	case '3':
		d = s[:1] + b[:3] + "00000" + b[3:5]
	case '4':
		d = s[:1] + b[:4] + "00000" + b[4:5]
	default:
		d = s[:1] + b[:5] + "0000" + b[5:]
	}
	d += s[7:]
	if ValidGTIN(d) {
		return d
	}
	return ""
}
func CanonicalGTIN(value, kind string) string {
	var b strings.Builder
	for _, c := range value {
		if c >= '0' && c <= '9' {
			b.WriteRune(c)
		}
	}
	s := b.String()
	if kind == "UPC_E" || kind == "UPCE" {
		s = ExpandUPCE(s)
	} else if len(s) == 8 && !ValidGTIN(s) {
		s = first(ExpandUPCE(s), s)
	}
	if ValidGTIN(s) {
		return s
	}
	return ""
}
func FatSecretGTIN(s string) string {
	s = CanonicalGTIN(s, "")
	if s == "" || len(s) == 14 {
		return ""
	}
	return strings.Repeat("0", 13-len(s)) + s
}

// Decode only retail EAN/UPC codes. QR links and arbitrary text never become a product ID.
func DecodeBarcode(data []byte) string {
	cfg, _, e := image.DecodeConfig(bytes.NewReader(data))
	if e != nil || cfg.Width <= 0 || cfg.Height <= 0 || int64(cfg.Width)*int64(cfg.Height) > 40_000_000 {
		return ""
	}
	img, _, e := image.Decode(bytes.NewReader(data))
	if e != nil {
		return ""
	}
	bitmap, e := gozxing.NewBinaryBitmapFromImage(img)
	if e != nil {
		return ""
	}
	hints := map[gozxing.DecodeHintType]any{gozxing.DecodeHintType_TRY_HARDER: true}
	r := oned.NewMultiFormatUPCEANReader(hints)
	result, e := r.Decode(bitmap, hints)
	if e != nil {
		return ""
	}
	code := CanonicalGTIN(result.GetText(), result.GetBarcodeFormat().String())
	if len(code) == 13 && (strings.HasPrefix(code, "978") || strings.HasPrefix(code, "979")) {
		return "" // Bookland EANs were ISBN13 in the previous decoder.
	}
	return code
}
func Plausible(n Nutrition) bool {
	for _, v := range []float64{n.Kcal, n.Protein, n.Fat, n.Carbs} {
		if math.IsNaN(v) || math.IsInf(v, 0) || v < 0 {
			return false
		}
	}
	if n.Kcal > 1000 || n.Protein > 100 || n.Fat > 100 || n.Carbs > 100 {
		return false
	}
	energy := 4*n.Protein + 9*n.Fat + 4*n.Carbs
	return energy < 1 || math.Abs(energy-n.Kcal) <= math.Max(80, .35*n.Kcal)
}
func StripBidi(s string) string {
	return strings.Map(func(r rune) rune {
		if r == 0x200e || r == 0x200f || r >= 0x202a && r <= 0x202e || r >= 0x2066 && r <= 0x2069 {
			return -1
		}
		return r
	}, s)
}
func Readable(s string) bool {
	letters := false
	for _, c := range StripBidi(s) {
		if unicode.IsLetter(c) {
			letters = true
			if !unicode.In(c, unicode.Latin, unicode.Cyrillic) {
				return false
			}
		}
	}
	return letters
}
func PickName(names ...string) string {
	fallback := ""
	for _, s := range names {
		s = strings.TrimSpace(StripBidi(s))
		if s == "" {
			continue
		}
		if Readable(s) {
			return s
		}
		if fallback == "" {
			fallback = s
		}
	}
	return fallback
}
func LoadTimezone(tz string) (*time.Location, error) {
	if tz == "" || tz == "Local" {
		return nil, fmt.Errorf("нужен явный часовой пояс IANA")
	}
	return time.LoadLocation(tz)
}
func Resolve(tz string, now time.Time, mealHint, dateHint string) (string, string, error) {
	loc, e := LoadTimezone(tz)
	if e != nil {
		return "", "", e
	}
	local := now.In(loc)
	day := local
	if local.Hour() < 4 {
		day = local.AddDate(0, 0, -1)
	}
	meal := "other"
	switch h := local.Hour(); {
	case h >= 4 && h < 11:
		meal = "breakfast"
	case h >= 11 && h < 16:
		meal = "lunch"
	case h >= 16 && h < 22:
		meal = "dinner"
	}
	if MealRU[mealHint] != "" {
		meal = mealHint
	}
	if dateHint == "yesterday" {
		day = day.AddDate(0, 0, -1)
	}
	return day.Format(time.DateOnly), meal, nil
}
func FatSecretDate(day string) (int64, error) {
	t, e := time.Parse(time.DateOnly, day)
	return t.Unix() / 86400, e
}

type Serving struct {
	ID, Description, MetricUnit                        string
	MetricAmount, Calories, Protein, Fat, Carbs, Units float64
}

func ParseServings(food Object) []Serving {
	var out []Serving
	for _, raw := range list(obj(food["servings"])["serving"]) {
		r := obj(raw)
		id := str(r["serving_id"])
		if id == "0" || id == "" {
			continue
		}
		units := num(r["number_of_units"])
		if units <= 0 {
			units = 1
		}
		out = append(out, Serving{id, strings.TrimSpace(str(r["serving_description"])), str(r["metric_serving_unit"]), num(r["metric_serving_amount"]), math.Max(0, num(r["calories"])), math.Max(0, num(r["protein"])), math.Max(0, num(r["fat"])), math.Max(0, num(r["carbohydrate"])), units})
	}
	return out
}
func (s Serving) Metric() bool {
	return s.MetricAmount > 0 && (s.MetricUnit == "g" || s.MetricUnit == "ml")
}

type Portion struct {
	Serving    Serving
	Multiplier float64
}

func (p Portion) APIUnits() float64 { return round(p.Multiplier*p.Serving.Units, 4) }
func (p Portion) Describe() string {
	if p.Serving.Metric() {
		return fmt.Sprintf("%g %s", round(p.Serving.MetricAmount*p.Multiplier, 1), p.Serving.MetricUnit)
	}
	return fmt.Sprintf("%g × %s", p.Multiplier, p.Serving.Description)
}
func DefaultPortion(ss []Serving, amount float64, unit string) *Portion {
	if unit == "g" || unit == "ml" {
		other := "ml"
		if unit == "ml" {
			other = "g"
		}
		for _, u := range []string{unit, other} {
			var pick *Serving
			for _, s := range ss {
				if s.Metric() && s.MetricUnit == u {
					copy := s
					if pick == nil || s.MetricAmount == 100 {
						pick = &copy
					}
					if s.MetricAmount == 100 {
						break
					}
				}
			}
			if pick != nil {
				return &Portion{*pick, round(amount/pick.MetricAmount, 6)}
			}
		}
	}
	if unit == "piece" && len(ss) > 0 {
		return &Portion{ss[0], amount}
	}
	return nil
}
func MatchesLabel(n Nutrition, ss []Serving, basis string) (bool, *float64) {
	return matchesLabel(n, ss, basis, true)
}
func matchesLabel(n Nutrition, ss []Serving, basis string, compareMacros bool) (bool, *float64) {
	for _, s := range ss {
		if !s.Metric() || s.MetricUnit != basis || s.Calories <= 0 {
			continue
		}
		scale := 100 / s.MetricAmount
		gap := func(a, b float64) float64 {
			if a <= 0 {
				return 0
			}
			return math.Abs(b-a) / a
		}
		worst := gap(n.Kcal, s.Calories*scale)
		if worst > .25 {
			return false, &worst
		}
		if !compareMacros {
			return true, &worst
		}
		for _, pair := range [][2]float64{{n.Protein, s.Protein * scale}, {n.Fat, s.Fat * scale}, {n.Carbs, s.Carbs * scale}} {
			if pair[0] < 1 && pair[1] < 1 {
				continue
			}
			g := gap(math.Max(pair[0], 1), pair[1])
			worst = math.Max(worst, g)
			if g > .4 {
				return false, &g
			}
		}
		return true, &worst
	}
	return true, nil
}

var amountPattern = regexp.MustCompile(`(?i)^\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:г|гр|g|мл|ml|шт|pieces?)?\s*$`)

func PositiveNumber(s string) (float64, bool) {
	m := amountPattern.FindStringSubmatch(s)
	if m == nil {
		return 0, false
	}
	n, ok := number(strings.ReplaceAll(m[1], ",", "."))
	return n, ok && n > 0 && n <= 100000
}
