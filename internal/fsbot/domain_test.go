package fsbot

import (
	"bytes"
	"image/png"
	"math"
	"net/url"
	"strings"
	"testing"

	"github.com/makiuchi-d/gozxing"
	"github.com/makiuchi-d/gozxing/oned"
	"github.com/makiuchi-d/gozxing/qrcode"
)

func TestOAuthRFC5849(t *testing.T) {
	p := url.Values{"b5": {"=%3D"}, "a3": {"a", "2 q"}, "c@": {""}, "a2": {"r b"}, "c2": {""}, "oauth_consumer_key": {"9djdj82h48djs9d2"}, "oauth_token": {"kkk9d7dh3k39sjv7"}, "oauth_signature_method": {"HMAC-SHA1"}, "oauth_timestamp": {"137131201"}, "oauth_nonce": {"7d8f3e4a"}}
	if got := OAuthSign("POST", "http://example.com/request", p, "j49sk3j29djd", "dh893hdasih9"); got != "r6/TJjbCOr97/+UU0NsvSne7s5g=" {
		t.Fatal(got)
	}
	if got := percent("/ =&+"); got != "%2F%20%3D%26%2B" {
		t.Fatal(got)
	}
	if OAuthBase("GET", "HTTPS://Platform.FatSecret.com:443/rest/server.api", nil) != OAuthBase("GET", "https://platform.fatsecret.com/rest/server.api", nil) {
		t.Fatal("default port")
	}
}
func TestBarcodesImages(t *testing.T) {
	for _, tc := range []struct {
		code   string
		format gozxing.BarcodeFormat
		writer gozxing.Writer
		want   string
	}{{"4006381333931", gozxing.BarcodeFormat_EAN_13, oned.NewEAN13Writer(), "4006381333931"}, {"96385074", gozxing.BarcodeFormat_EAN_8, oned.NewEAN8Writer(), "96385074"}, {"01234565", gozxing.BarcodeFormat_UPC_E, oned.NewUPCEWriter(), "012345000065"}, {"4006381333931", gozxing.BarcodeFormat_QR_CODE, qrcode.NewQRCodeWriter(), ""}} {
		t.Run(tc.code+tc.format.String(), func(t *testing.T) {
			matrix, e := tc.writer.Encode(tc.code, tc.format, 400, 150, nil)
			if e != nil {
				t.Fatal(e)
			}
			var b bytes.Buffer
			if e = png.Encode(&b, matrix); e != nil {
				t.Fatal(e)
			}
			if got := DecodeBarcode(b.Bytes()); got != tc.want {
				t.Fatalf("%s != %s", got, tc.want)
			}
		})
	}
	if DecodeBarcode([]byte("broken")) != "" {
		t.Fatal("invalid image")
	}
	if FatSecretGTIN("00012345600012") != "" {
		t.Fatal("GTIN14")
	}
}
func TestNutritionAndNames(t *testing.T) {
	for _, n := range []Nutrition{{Kcal: math.NaN()}, {Kcal: math.Inf(1)}, {Kcal: 100, Protein: -1}, {Kcal: 100, Protein: 60, Fat: 60, Carbs: 60}} {
		if Plausible(n) {
			t.Fatal(n)
		}
	}
	if !Plausible(Nutrition{}) {
		t.Fatal("zero calories")
	}
	if Readable("חומוס") || Readable("123") || !Readable("Coca-Cola Творог 5%") {
		t.Fatal("script detection")
	}
	if PickName("חומוס", "Hummus") != "Hummus" || strings.Contains(StripBidi("a\u202Eb"), "\u202E") {
		t.Fatal("bidi")
	}
}
func TestPortionsAndMatching(t *testing.T) {
	ss := []Serving{{ID: "1", Description: "1 cup", Calories: 100, Units: 1}}
	if DefaultPortion(ss, 200, "g") != nil {
		t.Fatal("grams must not become units")
	}
	p := DefaultPortion(ss, 1.5, "piece")
	if p == nil || p.APIUnits() != 1.5 {
		t.Fatal(p)
	}
	ss = []Serving{{ID: "1", MetricUnit: "g", MetricAmount: 100, Calories: 186, Protein: 12, Fat: 10, Carbs: 12, Units: 100}}
	p = DefaultPortion(ss, 450, "ml")
	if p.APIUnits() != 450 || p.Describe() != "450 g" {
		t.Fatal(p)
	}
	if ok, _ := MatchesLabel(Nutrition{Kcal: 186, Protein: 12, Fat: 10, Carbs: 12}, ss, "g"); !ok {
		t.Fatal("same product")
	}
	ss[0].Protein = 25.7
	if ok, _ := MatchesLabel(Nutrition{Kcal: 186, Protein: 12, Fat: 10, Carbs: 12}, ss, "g"); ok {
		t.Fatal("different macros")
	}
}
func TestPositiveNumber(t *testing.T) {
	for _, s := range []string{"150", "1,5", "450 мл", "2 pieces"} {
		if _, ok := PositiveNumber(s); !ok {
			t.Fatal(s)
		}
	}
	for _, s := range []string{"0", "-1", "NaN", "Inf", "100001", "1e3", "food"} {
		if _, ok := PositiveNumber(s); ok {
			t.Fatal(s)
		}
	}
}
