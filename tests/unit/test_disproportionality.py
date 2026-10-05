import pytest

from trialsentinel.rules.disproportionality import compute_disproportionality


def test_known_table_matches_hand_calculation() -> None:
    # a=20, b=80, c=100, d=9800
    stats = compute_disproportionality(a=20, n_drug=100, n_event=120, n_total=10_000)
    assert (stats.a, stats.b, stats.c, stats.d) == (20, 80, 100, 9800)
    assert stats.prr == pytest.approx(19.8)
    assert stats.ror == pytest.approx(24.5)
    assert stats.ror_ci_low == pytest.approx(14.45, abs=0.05)
    assert stats.chi2 is not None and stats.chi2 > 4
    assert stats.is_signal


def test_fewer_than_three_reports_is_never_a_signal() -> None:
    stats = compute_disproportionality(a=2, n_drug=10, n_event=20, n_total=100_000)
    assert stats.prr is not None and stats.prr > 2
    assert not stats.is_signal


def test_zero_cell_leaves_ror_undefined() -> None:
    stats = compute_disproportionality(a=0, n_drug=50, n_event=40, n_total=10_000)
    assert stats.ror is None
    assert stats.prr == 0.0
    assert not stats.is_signal


def test_inconsistent_counts_are_rejected() -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        compute_disproportionality(a=10, n_drug=5, n_event=20, n_total=1_000)
