package fsbot

import (
	"fmt"
	"html"
	"strconv"
	"strings"
)

type Button struct {
	Text string `json:"text"`
	Data string `json:"callback_data"`
}
type Keyboard struct {
	Rows [][]Button `json:"inline_keyboard"`
}

func button(id int64, action, arg, text string) Button {
	return Button{text, fmt.Sprintf("%d:%s:%s", id, action, arg)}
}
func ParseCallback(data string) (int64, string, string, error) {
	parts := strings.SplitN(data, ":", 3)
	if len(parts) != 3 {
		return 0, "", "", fmt.Errorf("неизвестная кнопка")
	}
	id, e := strconv.ParseInt(parts[0], 10, 64)
	if e != nil || id <= 0 || len(parts[1]) != 1 || !strings.Contains("wexicgmdbnrs", parts[1]) {
		return 0, "", "", fmt.Errorf("неизвестная кнопка")
	}
	return id, parts[1], parts[2], nil
}
func DraftKeyboard(id int64, d *Draft) Keyboard {
	text, action := "✅ Записать", "w"
	if d.NeedsReview {
		text, action = "⚠️ Проверить", "r"
	}
	k := Keyboard{[][]Button{{button(id, action, "", text), button(id, "e", "", "✏️ Изменить"), button(id, "x", "", "❌ Отмена")}}}
	for p, i := range d.Items {
		arg := strconv.Itoa(p)
		if i.Creatable != nil && (i.Status == "pending" || i.Status == "") && (i.FoodID == "" || i.Mismatch != nil) {
			k.Rows = append(k.Rows, []Button{button(id, "n", arg, "➕ Создать «"+short(i.Name, 24)+"»")})
		}
		if i.NeedsPortion {
			k.Rows = append(k.Rows, []Button{button(id, "i", arg, fmt.Sprintf("⚖️ Выбрать порцию для %d", p+1))})
		}
	}
	return k
}
func ReviewKeyboard(id int64) Keyboard {
	return Keyboard{[][]Button{{button(id, "w", "", "⚠️ Записать всё равно")}, {button(id, "e", "", "✏️ Изменить"), button(id, "x", "", "❌ Отмена")}}}
}
func EditKeyboard(id int64, d *Draft) Keyboard {
	k := Keyboard{}
	for p, i := range d.Items {
		k.Rows = append(k.Rows, []Button{button(id, "i", strconv.Itoa(p), fmt.Sprintf("%d. %s", p+1, short(i.Name, 28)))})
	}
	k.Rows = append(k.Rows, []Button{button(id, "m", "", "🍽 Приём пищи"), button(id, "d", "", "📅 Дата")}, []Button{button(id, "b", "", "← Назад")})
	return k
}
func ItemKeyboard(id int64, p int, i *Item) Keyboard {
	k := Keyboard{}
	for _, s := range i.Available {
		k.Rows = append(k.Rows, []Button{button(id, "s", fmt.Sprintf("%d.%s", p, s.ID), "⚖️ "+short(s.Description, 35))})
	}
	for c, v := range i.Candidates[:min(5, len(i.Candidates))] {
		mark := ""
		if c == i.Chosen {
			mark = "• "
		}
		k.Rows = append(k.Rows, []Button{button(id, "c", fmt.Sprintf("%d.%d", p, c), mark+short(v.Title, 40))})
	}
	k.Rows = append(k.Rows, []Button{button(id, "g", strconv.Itoa(p), "⚖️ Указать количество")}, []Button{button(id, "e", "", "← Назад")})
	return k
}
func MealKeyboard(id int64) Keyboard {
	k := Keyboard{}
	for _, meal := range Meals {
		k.Rows = append(k.Rows, []Button{button(id, "m", meal, MealRU[meal])})
	}
	k.Rows = append(k.Rows, []Button{button(id, "e", "", "← Назад")})
	return k
}
func DateKeyboard(id int64) Keyboard {
	return Keyboard{[][]Button{{button(id, "d", "today", "Сегодня")}, {button(id, "d", "yesterday", "Вчера")}, {button(id, "e", "", "← Назад")}}}
}
func RenderDraft(d *Draft) string {
	lines := []string{}
	total := 0.0
	for p, i := range d.Items {
		name := html.EscapeString(i.Name)
		prefix := fmt.Sprintf("%d. ", p+1)
		switch {
		case i.Status == "undone":
			lines = append(lines, prefix+"<b>"+name+"</b> — ↩️ удалено из дневника")
		case i.Uncertain():
			lines = append(lines, prefix+"<b>"+name+"</b> — ⚠️ исход предыдущего запроса неизвестен; проверь FatSecret")
		case i.NeedsPortion:
			lines = append(lines, fmt.Sprintf("%s<b>%s</b> — %g %s\n    ⚖️ У продукта нет веса порции. Выбери порцию и укажи количество.", prefix, html.EscapeString(first(i.Title, i.Name)), i.Amount, i.Unit))
		case i.FoodID == "":
			if s := i.Creatable; s != nil {
				scale := i.Amount / 100
				kcal := round(s.Kcal*scale, 1)
				total += kcal
				origin := "с этикетки"
				if i.Source != "" {
					origin = "источник: " + html.EscapeString(i.Source)
				}
				basis := "100 г"
				if s.BasisUnit == "ml" {
					basis = "100 мл"
				}
				lines = append(lines, fmt.Sprintf("%s<b>%s</b> — %g %s · в базе FatSecret нет, %s\n    %g ккал · Б %g · Ж %g · У %g\n    данные с этикетки на %s\n    ➕ создай продукт кнопкой ниже, иначе пункт не запишется", prefix, name, i.Amount, i.Unit, origin, kcal, round(s.Protein*scale, 1), round(s.Fat*scale, 1), round(s.Carbs*scale, 1), basis))
			} else {
				lines = append(lines, prefix+"<b>"+name+"</b> — "+html.EscapeString(first(i.Error, "не нашёл в базе FatSecret")))
			}
		default:
			total += i.Kcal
			mark := ""
			if i.Status == "written" {
				mark = "✅ уже записано · "
			}
			line := fmt.Sprintf("%s%s<b>%s</b> — %s\n    %g ккал · Б %g · Ж %g · У %g", prefix, mark, html.EscapeString(i.Title), html.EscapeString(i.Portion), i.Kcal, i.Protein, i.Fat, i.Carbs)
			if i.Mismatch != nil {
				basis := "г"
				if i.LabelBasis == "ml" {
					basis = "мл"
				}
				line += fmt.Sprintf("\n    ⚠️ на этикетке %g ккал/100 %s — расхождение %.0f%%, похоже, это другой продукт", i.LabelKcal, basis, *i.Mismatch*100)
			}
			lines = append(lines, line)
		}
	}
	lines = append(lines, fmt.Sprintf("\n<b>Итого: %g ккал</b> · %s · %s", total, MealRU[d.Meal], d.Day), fmt.Sprintf("Уверенность распознавания: %.0f%%", round(d.Confidence*100, 0)))
	if d.NeedsReview {
		lines = append(lines, "⚠️ Низкая уверенность: проверь продукт и количество перед записью.")
	}
	return strings.Join(lines, "\n")
}

const HelpText = `Пишу еду в твой дневник FatSecret.

<b>Как пользоваться</b>
• Текстом: «творог 5% 200г и овсянка 60г»
• Фото тарелки — оценю блюда и вес
• Фото упаковки — прочитаю название и найду продукт
• Штрих-кодом: пришли цифры под кодом или фото самого кода

Перед записью показываю, что нашёл, — жмёшь «Записать» или правишь.

<b>Команды</b>
/link — привязать аккаунт FatSecret
/tz — часовой пояс
/undo — отменить последнюю запись
/attribution — об источнике данных

Если продукта нет в базе — пришли фото этикетки, создам его в твоём аккаунте.`
const Attribution = `Данные о продуктах и дневник — <b>fatsecret</b>.
Powered by fatsecret · https://platform.fatsecret.com

Точную формулировку атрибуции нужно сверить с Terms and Conditions FatSecret — пока здесь заглушка.`
const BarcodeUnknown = `Такого штрих-кода нет в базе FatSecret.

Пришли фото упаковки с таблицей пищевой ценности — прочитаю КБЖУ, предложу создать продукт в твоём аккаунте и запомню этот код: в следующий раз хватит сканирования.`
