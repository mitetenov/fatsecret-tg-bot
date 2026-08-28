"""Разбор ответа LLM: бесплатные модели схему не гарантируют, поэтому проверяем мусор."""

import pytest

from fsbot.llm.parsing import ParseError, parse_recognition

CLEAN = """{"kind":"text","items":[
 {"query_en":"cottage cheese 5%","name_ru":"творог 5%","amount":200,"unit":"g"},
 {"query_en":"rolled oats","name_ru":"овсянка","amount":60,"unit":"g"}]}"""


def test_clean_json():
    result = parse_recognition(CLEAN)
    assert result.kind == "text"
    assert [item.name_ru for item in result.items] == ["творог 5%", "овсянка"]
    assert result.items[0].amount == 200


def test_json_wrapped_in_prose_and_fences():
    raw = 'Вот результат:\n```json\n{"kind":"plate","items":[{"query_en":"omelette",' \
          '"name_ru":"омлет","amount":120,"unit":"g"}]}\n```\nГотово.'
    result = parse_recognition(raw)
    assert result.kind == "plate"
    assert result.items[0].query_en == "omelette"


def test_units_and_meals_are_normalized():
    raw = """{"kind":"text","items":[
      {"query_en":"milk","name_ru":"молоко","amount":"250","unit":"мл","meal":"DINNER"},
      {"query_en":"egg","name_ru":"яйцо","amount":2,"unit":"шт","meal":"brunch"}]}"""
    milk, egg = parse_recognition(raw).items
    assert milk.unit == "ml"
    assert milk.meal == "dinner"
    assert egg.unit == "piece"
    assert egg.meal is None  # неизвестный приём пищи не протаскиваем дальше


def test_broken_items_are_dropped_but_good_ones_survive():
    raw = """{"kind":"text","items":[
      {"query_en":"","name_ru":"","amount":100,"unit":"g"},
      {"query_en":"rice","name_ru":"рис","amount":"много","unit":"g"},
      {"query_en":"rice","name_ru":"рис","amount":-5,"unit":"g"},
      {"query_en":"rice","name_ru":"рис","amount":150,"unit":"g"}]}"""
    items = parse_recognition(raw).items
    assert len(items) == 1
    assert items[0].amount == 150


def test_unknown_kind_falls_back_to_text():
    raw = '{"kind":"хз","items":[{"query_en":"tea","name_ru":"чай","amount":200,"unit":"ml"}]}'
    assert parse_recognition(raw).kind == "text"


def test_date_hint_yesterday_survives_and_garbage_does_not():
    raw = """{"kind":"text","items":[
      {"query_en":"soup","name_ru":"суп","amount":300,"unit":"g","date_hint":"YESTERDAY"},
      {"query_en":"tea","name_ru":"чай","amount":200,"unit":"ml","date_hint":"позавчера"}]}"""
    soup, tea = parse_recognition(raw).items
    assert soup.date_hint == "yesterday"
    assert tea.date_hint is None


@pytest.mark.parametrize("raw", ["не знаю", "", "{}", '{"kind":"text","items":[]}'])
def test_no_usable_items_is_an_error(raw: str):
    with pytest.raises(ParseError):
        parse_recognition(raw)


def test_label_gives_brand_barcode_and_nutrition():
    raw = """{"kind":"label","barcode":"4 820000 000000","items":[
      {"query_en":"sante milk 3.2","name_ru":"Молоко Sante 3.2%","amount":250,"unit":"ml",
       "brand":"Sante","kcal_100g":60,"protein_100g":3,"fat_100g":3.2,"carbs_100g":4.7}]}"""
    result = parse_recognition(raw)
    assert result.barcode == "4820000000000"  # пробелы из вёрстки кода отброшены
    item = result.items[0]
    assert item.brand == "Sante"
    assert item.nutrition.kcal == 60
    assert item.nutrition.carbs == 4.7


def test_partial_nutrition_is_refused():
    # Неполные КБЖУ выглядят достоверно, но дают неверный продукт навсегда:
    # удалить созданное через API нельзя.
    raw = """{"kind":"label","items":[{"query_en":"x","name_ru":"Икс","amount":100,
      "unit":"g","kcal_100g":100,"protein_100g":5}]}"""
    assert parse_recognition(raw).items[0].nutrition is None


def test_impossible_complete_nutrition_is_refused():
    raw = """{"kind":"label","items":[{"query_en":"x","name_ru":"Икс",
      "amount":100,"unit":"g","kcal_100g":50,"protein_100g":25,
      "fat_100g":25,"carbs_100g":25}]}"""
    assert parse_recognition(raw).items[0].nutrition is None


def test_implausible_barcode_is_dropped():
    raw = """{"kind":"label","barcode":"12","items":[
      {"query_en":"x","name_ru":"Икс","amount":100,"unit":"g"}]}"""
    assert parse_recognition(raw).barcode is None
