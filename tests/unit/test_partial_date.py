from datetime import date

import pytest

from trialsentinel.ingestion.models import DatePrecision, PartialDate


@pytest.mark.parametrize(
    ("raw", "earliest", "latest", "precision"),
    [
        ("2021-03-15", date(2021, 3, 15), date(2021, 3, 15), DatePrecision.DAY),
        ("2021-02", date(2021, 2, 1), date(2021, 2, 28), DatePrecision.MONTH),
        ("2020-02", date(2020, 2, 1), date(2020, 2, 29), DatePrecision.MONTH),  # leap year
        ("2019", date(2019, 1, 1), date(2019, 12, 31), DatePrecision.YEAR),
    ],
)
def test_parse_keeps_precision(raw: str, earliest: date, latest: date, precision: str) -> None:
    parsed = PartialDate.parse(raw)
    assert parsed is not None
    assert parsed.earliest == earliest
    assert parsed.latest == latest
    assert parsed.precision == precision


@pytest.mark.parametrize("raw", [None, "", "   ", "not-a-date", "2021-13", "2021-02-30"])
def test_invalid_dates_return_none(raw: str | None) -> None:
    assert PartialDate.parse(raw) is None
