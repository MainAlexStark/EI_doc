"""Тесты нумерации протоколов.

Главное требование: номера идут строго по времени поверки, при этом поверки
приходят не по порядку. Эти тесты проверяют, что хронология восстанавливается
сама, а запечатанные номера остаются неприкосновенными.
"""

from __future__ import annotations

import pytest
from django.test import TestCase

from apps.verification import numbering
from apps.verification.models import ProtocolStatus
from apps.verification.numbering import NumberingError
from apps.verification.tests import factories as f


class NumberingTestCase(TestCase):
    def setUp(self) -> None:
        self.family = f.make_family()
        self.employee = f.make_employee()
        f.attest(self.employee, self.family)

    def scope(self, year: int = 2026):
        return numbering.NumberingScope.objects.get(
            series=self.family.series, employee=self.employee, year=year
        )

    def numbers(self):
        """Номера в порядке возрастания, как они лягут в журнал."""
        return [
            p.full_number
            for p in numbering.Protocol.objects.filter(scope=self.scope())
            .exclude(seq=None)
            .order_by("seq", "suffix")
        ]

    # -- базовая нумерация ------------------------------------------------
    def test_numbers_follow_verification_time_not_entry_order(self):
        """Поверки внесены вразнобой — номера всё равно по хронологии."""
        third = f.draft(self.family, self.employee, day=3)
        first = f.draft(self.family, self.employee, day=1)
        second = f.draft(self.family, self.employee, day=2)

        numbering.assign_numbers(self.scope())

        first.refresh_from_db(), second.refresh_from_db(), third.refresh_from_db()
        assert (first.seq, second.seq, third.seq) == (1, 2, 3)
        assert first.full_number == "ЕИ-03-05-00001"

    def test_assign_is_idempotent(self):
        f.draft(self.family, self.employee, day=1)
        f.draft(self.family, self.employee, day=2)

        numbering.assign_numbers(self.scope())
        second_run = numbering.assign_numbers(self.scope())

        assert not second_run.changed
        assert self.numbers() == ["ЕИ-03-05-00001", "ЕИ-03-05-00002"]

    def test_draft_becomes_numbered(self):
        protocol = f.draft(self.family, self.employee, day=1)
        numbering.assign_numbers(self.scope())
        protocol.refresh_from_db()
        assert protocol.status == ProtocolStatus.NUMBERED
        assert protocol.numbered_at is not None

    def test_dry_run_changes_nothing(self):
        protocol = f.draft(self.family, self.employee, day=1)
        result = numbering.assign_numbers(self.scope(), dry_run=True)
        protocol.refresh_from_db()

        assert result.changed
        assert protocol.seq is None
        assert protocol.status == ProtocolStatus.DRAFT

    # -- вставка задним числом в открытый хвост ---------------------------
    def test_forgotten_verification_takes_its_chronological_place(self):
        """Забытая поверка от 2-го числа, внесённая после 3-го, сдвигает хвост."""
        f.draft(self.family, self.employee, day=1)
        day3 = f.draft(self.family, self.employee, day=3)
        numbering.assign_numbers(self.scope())
        day3.refresh_from_db()
        assert day3.seq == 2

        forgotten = f.draft(self.family, self.employee, day=2)
        result = numbering.assign_numbers(self.scope())

        forgotten.refresh_from_db()
        day3.refresh_from_db()
        assert forgotten.seq == 2
        assert day3.seq == 3, "третье число должно было сдвинуться"
        assert len(result.renumbered) == 1
        assert not numbering.chronology_breaks(self.scope())

    # -- запечатанная зона -------------------------------------------------
    def test_signed_protocols_are_never_renumbered(self):
        day1 = f.draft(self.family, self.employee, day=1)
        day5 = f.draft(self.family, self.employee, day=5)
        numbering.assign_numbers(self.scope())
        numbering.seal(day1, signed_by=self.employee)
        numbering.seal(day5, signed_by=self.employee)

        # поверка задним числом между ними — в запечатанную зону её пускать нельзя
        f.draft(self.family, self.employee, day=3)
        numbering.assign_numbers(self.scope())

        day1.refresh_from_db()
        day5.refresh_from_db()
        assert (day1.seq, day5.seq) == (1, 2), "подписанные номера сдвинулись"
        assert self.numbers()[-1] == "ЕИ-03-05-00003"

    def test_new_verifications_continue_after_sealed_high_water(self):
        sealed = f.draft(self.family, self.employee, day=1)
        numbering.assign_numbers(self.scope())
        numbering.seal(sealed, signed_by=self.employee)

        f.draft(self.family, self.employee, day=2)
        numbering.assign_numbers(self.scope())

        assert self.numbers() == ["ЕИ-03-05-00001", "ЕИ-03-05-00002"]

    # -- дробные подномера -------------------------------------------------
    def test_out_of_sequence_insert_gets_fraction_suffix(self):
        day1 = f.draft(self.family, self.employee, day=1)
        day5 = f.draft(self.family, self.employee, day=5)
        numbering.assign_numbers(self.scope())
        numbering.seal(day1, signed_by=self.employee)
        numbering.seal(day5, signed_by=self.employee)

        late = f.make_verification(self.family, self.employee, day=3, serial="SN903")
        inserted = numbering.insert_out_of_sequence(late, reason="Найден бумажный бланк от 03.03")

        assert inserted.full_number == "ЕИ-03-05-00001/1"
        assert self.numbers() == ["ЕИ-03-05-00001", "ЕИ-03-05-00001/1", "ЕИ-03-05-00002"]

    def test_second_insert_gets_next_fraction(self):
        day1 = f.draft(self.family, self.employee, day=1)
        numbering.assign_numbers(self.scope())
        numbering.seal(day1, signed_by=self.employee)

        for day, serial in ((2, "SN902"), (3, "SN903")):
            v = f.make_verification(self.family, self.employee, day=day, serial=serial)
            numbering.insert_out_of_sequence(v, reason="дозаведение")

        assert self.numbers() == ["ЕИ-03-05-00001", "ЕИ-03-05-00001/1", "ЕИ-03-05-00001/2"]

    def test_out_of_sequence_requires_reason(self):
        v = f.make_verification(self.family, self.employee, day=3)
        with pytest.raises(NumberingError, match="причина"):
            numbering.insert_out_of_sequence(v, reason="")

    def test_out_of_sequence_refused_when_tail_would_handle_it(self):
        """Если запечатанных предшественников нет, обычный пересчёт справится сам."""
        f.draft(self.family, self.employee, day=5)
        v = f.make_verification(self.family, self.employee, day=1, serial="SN901")
        with pytest.raises(NumberingError, match="пересчёт хвоста"):
            numbering.insert_out_of_sequence(v, reason="дозаведение")

    def test_subnumbers_are_not_renumbered_by_tail_pass(self):
        day1 = f.draft(self.family, self.employee, day=1)
        numbering.assign_numbers(self.scope())
        numbering.seal(day1, signed_by=self.employee)
        v = f.make_verification(self.family, self.employee, day=2, serial="SN902")
        inserted = numbering.insert_out_of_sequence(v, reason="дозаведение")

        f.draft(self.family, self.employee, day=9, serial="SN909")
        numbering.assign_numbers(self.scope())

        inserted.refresh_from_db()
        assert inserted.full_number == "ЕИ-03-05-00001/1"

    # -- подпись -----------------------------------------------------------
    def test_cannot_sign_without_attestation(self):
        other = f.make_employee(tab="07", name="Иванов И. И.")
        protocol = f.draft(self.family, self.employee, day=1)
        numbering.assign_numbers(self.scope())

        with pytest.raises(NumberingError, match="аттестации"):
            numbering.seal(protocol, signed_by=other)

    def test_cannot_sign_protocol_without_number(self):
        protocol = f.draft(self.family, self.employee, day=1)
        with pytest.raises(NumberingError, match="без номера"):
            numbering.seal(protocol, signed_by=self.employee)

    def test_cannot_sign_twice(self):
        protocol = f.draft(self.family, self.employee, day=1)
        numbering.assign_numbers(self.scope())
        numbering.seal(protocol, signed_by=self.employee)
        with pytest.raises(NumberingError, match="запечатан"):
            numbering.seal(protocol, signed_by=self.employee)

    # -- аннулирование -----------------------------------------------------
    def test_void_keeps_the_number_occupied(self):
        first = f.draft(self.family, self.employee, day=1)
        second = f.draft(self.family, self.employee, day=2)
        numbering.assign_numbers(self.scope())
        numbering.seal(first, signed_by=self.employee)
        numbering.seal(second, signed_by=self.employee)

        numbering.void(first, reason="Ошибка в заводском номере")
        f.draft(self.family, self.employee, day=3)
        numbering.assign_numbers(self.scope())

        first.refresh_from_db()
        assert first.seq == 1, "аннулированный протокол освободил номер — дыра в журнале"
        assert self.numbers() == ["ЕИ-03-05-00001", "ЕИ-03-05-00002", "ЕИ-03-05-00003"]

    # -- публикация в ФИФ ---------------------------------------------------
    def test_only_signed_protocols_go_to_fif(self):
        protocol = f.draft(self.family, self.employee, day=1)
        numbering.assign_numbers(self.scope())
        with pytest.raises(NumberingError, match="подписанные"):
            numbering.publish(protocol, fif_record_number="1-2026-0001")

        numbering.seal(protocol, signed_by=self.employee)
        numbering.publish(protocol, fif_record_number="1-2026-0001")
        protocol.refresh_from_db()
        assert protocol.status == ProtocolStatus.PUBLISHED

    # -- изоляция областей --------------------------------------------------
    def test_scopes_are_independent_per_verifier(self):
        other = f.make_employee(tab="07", name="Иванов И. И.")
        f.attest(other, self.family)
        f.draft(self.family, self.employee, day=1, serial="SN101")
        f.draft(self.family, other, day=1, serial="SN201")

        for scope in numbering.NumberingScope.objects.all():
            numbering.assign_numbers(scope)

        all_numbers = sorted(
            p.full_number for p in numbering.Protocol.objects.exclude(seq=None)
        )
        assert all_numbers == ["ЕИ-03-05-00001", "ЕИ-03-07-00001"]

    def test_numbering_resets_at_the_start_of_the_year(self):
        """Счётчик обнуляется 1 января: декабрьская и январская поверки — оба 00001."""
        december = f.draft(
            self.family, self.employee, day=29, month=12, year=2025, serial="SN1229"
        )
        january = f.draft(self.family, self.employee, day=9, month=1, serial="SN0109")

        numbering.assign_numbers(self.scope(2025))
        numbering.assign_numbers(self.scope(2026))

        december.refresh_from_db()
        january.refresh_from_db()
        assert december.full_number == "ЕИ-03-05-00001"
        assert january.full_number == "ЕИ-03-05-00001"
        assert december.scope_id != january.scope_id

    def test_december_verification_does_not_shift_january_numbers(self):
        """Забытая декабрьская поверка не трогает нумерацию нового года."""
        january = f.draft(self.family, self.employee, day=9, month=1, serial="SN0109")
        numbering.assign_numbers(self.scope(2026))

        f.draft(self.family, self.employee, day=30, month=12, year=2025, serial="SN1230")
        numbering.assign_numbers(self.scope(2025))

        january.refresh_from_db()
        assert january.seq == 1

    def test_chronology_breaks_reports_only_real_problems(self):
        f.draft(self.family, self.employee, day=1)
        f.draft(self.family, self.employee, day=2)
        numbering.assign_numbers(self.scope())
        assert numbering.chronology_breaks(self.scope()) == []
