"""Frequentist disproportionality statistics for spontaneous-report (FAERS) data.

2x2 table for drug D and adverse event E (counts are reports, not patients):

                 E        not E
    D            a          b
    not D        c          d

Signal criterion (Evans et al., 2001): a >= 3, PRR >= 2, Yates chi-squared >= 4.
These statistics generate hypotheses; they are not evidence of causation.
"""

import math
from dataclasses import dataclass

Z_95 = 1.959963984540054


@dataclass(frozen=True)
class Disproportionality:
    a: int
    b: int
    c: int
    d: int
    prr: float | None
    ror: float | None
    ror_ci_low: float | None
    ror_ci_high: float | None
    chi2: float | None

    @property
    def is_signal(self) -> bool:
        return (
            self.a >= 3
            and self.prr is not None
            and self.prr >= 2
            and self.chi2 is not None
            and self.chi2 >= 4
        )


def compute_disproportionality(
    *, a: int, n_drug: int, n_event: int, n_total: int
) -> Disproportionality:
    b = n_drug - a
    c = n_event - a
    d = n_total - n_drug - n_event + a
    if min(a, b, c, d) < 0:
        raise ValueError(f"inconsistent counts: a={a} b={b} c={c} d={d}")

    prr = None
    if a + b > 0 and c > 0 and c + d > 0:
        prr = (a / (a + b)) / (c / (c + d))

    ror = ror_low = ror_high = None
    if min(a, b, c, d) > 0:
        ror = (a * d) / (b * c)
        se = math.sqrt(1 / a + 1 / b + 1 / c + 1 / d)
        ror_low = math.exp(math.log(ror) - Z_95 * se)
        ror_high = math.exp(math.log(ror) + Z_95 * se)

    chi2 = None
    n = a + b + c + d
    denominator = (a + b) * (c + d) * (a + c) * (b + d)
    if denominator > 0:
        chi2 = n * max(abs(a * d - b * c) - n / 2, 0) ** 2 / denominator

    return Disproportionality(a, b, c, d, prr, ror, ror_low, ror_high, chi2)
