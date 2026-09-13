"""Разбор ответа реестра ФИФ.

Схема ответа публично не документирована, поэтому парсер терпим к обёрткам и
именам полей. Эти тесты фиксируют именно эту терпимость — и главное свойство:
одинаковые наименования от разных изготовителей помечаются как неоднозначные.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.arshin.fif import FifClient, FifType, mark_ambiguous


class RowsExtractionTestCase(SimpleTestCase):
    def test_plain_list(self):
        assert list(FifClient._rows([{"a": 1}, {"b": 2}])) == [{"a": 1}, {"b": 2}]

    def test_common_wrappers(self):
        for key in ("result", "results", "items", "rows", "data"):
            assert list(FifClient._rows({key: [{"a": 1}]})) == [{"a": 1}]

    def test_nested_wrapper(self):
        assert list(FifClient._rows({"response": {"items": [{"a": 1}]}})) == [{"a": 1}]

    def test_unknown_shape_gives_nothing_instead_of_crashing(self):
        assert list(FifClient._rows({"unexpected": 42})) == []
        assert list(FifClient._rows("не json")) == []


class ParseTestCase(SimpleTestCase):
    def test_picks_fields_by_alias_and_ignores_case(self):
        parsed = FifClient._parse(
            {
                "Number": "12345-06",
                "title": "Счётчики воды крыльчатые СВК",
                "manufactorer": 'ООО "Тепловодомер"',
                "mpi": "72",
                "extra": "останется в raw",
            }
        )
        assert parsed.registry_number == "12345-06"
        assert parsed.name == "Счётчики воды крыльчатые СВК"
        assert parsed.manufacturer == 'ООО "Тепловодомер"'
        assert parsed.interval_months == 72
        assert parsed.raw["extra"] == "останется в raw"

    def test_missing_fields_do_not_crash(self):
        parsed = FifClient._parse({"совсем": "другое"})
        assert parsed.registry_number == ""
        assert parsed.interval_months is None
        assert "без наименования" in parsed.label

    def test_label_always_names_the_manufacturer(self):
        known = FifType(registry_number="1-06", name="СВК-15", manufacturer="Тепловодомер")
        unknown = FifType(registry_number="1-06", name="СВК-15")
        assert "Тепловодомер" in known.label
        assert "изготовитель не указан" in unknown.label


class AmbiguityTestCase(SimpleTestCase):
    def test_same_name_from_different_manufacturers_is_flagged(self):
        """Тот самый случай: «СВК-15» у трёх изготовителей."""
        candidates = [
            FifType(registry_number="1-06", name="СВК-15", manufacturer="Тепловодомер"),
            FifType(registry_number="2-09", name="СВК-15", manufacturer="Бетар"),
            FifType(registry_number="3-11", name="ВСХ-15", manufacturer="Метер"),
        ]
        result = mark_ambiguous(candidates)

        assert [item["ambiguous"] for item in result] == [True, True, False]
        assert all(item["manufacturer"] for item in result[:2])

    def test_case_and_spacing_do_not_hide_a_duplicate(self):
        candidates = [
            FifType(registry_number="1-06", name="СВК-15", manufacturer="А"),
            FifType(registry_number="2-09", name=" свк-15 ", manufacturer="Б"),
        ]
        assert [item["ambiguous"] for item in mark_ambiguous(candidates)] == [True, True]
