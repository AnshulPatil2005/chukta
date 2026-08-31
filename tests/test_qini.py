"""Qini implementation, validated against hand-computed cases.

Why this file exists: every number in this project's results comes out of a
simulator I wrote, scored by a metric I also wrote. If the metric is wrong, the
sensitivity sweep and the decile table are wrong in a way nothing else would
catch - they would still be internally consistent.

So the curve is checked against cases whose answer is computable on paper,
independent of the simulator, plus the structural properties Radcliffe & Surry
(TR-2011-1) define: a ranking that beats random scores positive, random scores
zero, and an inverted ranking scores negative.

The worked example below is the load-bearing one. If someone changes the
trapezoid rule or the normalisation, it fails with an arithmetic error rather
than a vague drift.
"""

from __future__ import annotations

import pytest

from eval.uplift import QiniCurve, qini_coefficient, qini_from_rows


def rows(pairs: list[tuple[float, float]]) -> list[dict]:
    """(score, uplift) -> the row shape the curve consumes."""
    return [{"score": s, "uplift_rupees": u} for s, u in pairs]


# -- the worked example ------------------------------------------------------


def test_matches_a_curve_computed_by_hand():
    """Four units, scores 4>3>2>1, uplifts 10, 5, 0, -3.

        cumulative  = [0, 10, 15, 15, 12]
        fractions   = [0, .25, .5, .75, 1]

    Trapezoid area under the model curve:
        (0+10)/2*.25  = 1.250
        (10+15)/2*.25 = 3.125
        (15+15)/2*.25 = 3.750
        (15+12)/2*.25 = 3.375
                        -----
                        11.500

    Random diagonal ends at the same final value, so its area is 12/2 = 6.
    Coefficient = (11.5 - 6) / 12 = 0.4583...
    """
    c = qini_from_rows(rows([(4, 10), (3, 5), (2, 0), (1, -3)]))

    assert c.cumulative == pytest.approx([0, 10, 15, 15, 12])
    assert c.fractions == pytest.approx([0, 0.25, 0.5, 0.75, 1.0])
    assert c.final_value == pytest.approx(12)
    assert qini_coefficient(c) == pytest.approx(5.5 / 12)


def test_peak_is_where_cumulative_gain_is_highest():
    """The curve rises over persuadables and turns down over sleeping dogs.
    Its peak is the fraction worth targeting."""
    c = qini_from_rows(rows([(4, 10), (3, 5), (2, 0), (1, -3)]))
    assert c.peak_value == pytest.approx(15)
    assert c.peak_fraction == pytest.approx(0.5)
    assert c.value_left_on_table == pytest.approx(3)  # 15 - 12


# -- structural properties ---------------------------------------------------


def test_perfect_ranking_beats_random():
    good = qini_from_rows(rows([(4, 10), (3, 6), (2, 2), (1, -4)]))
    assert qini_coefficient(good) > 0


def test_inverted_ranking_scores_negative():
    """Same uplifts, ranking reversed. Targeting the worst cases first is worse
    than targeting at random, and the coefficient has to say so."""
    good = rows([(4, 10), (3, 6), (2, 2), (1, -4)])
    bad = rows([(1, 10), (2, 6), (3, 2), (4, -4)])
    assert qini_coefficient(qini_from_rows(good)) > 0
    assert qini_coefficient(qini_from_rows(bad)) < 0


def test_a_ranking_uncorrelated_with_uplift_scores_about_zero():
    """Constant uplift means the ordering carries no information, so the curve
    IS the diagonal and the coefficient is exactly zero."""
    c = qini_from_rows(rows([(4, 5), (3, 5), (2, 5), (1, 5)]))
    assert qini_coefficient(c) == pytest.approx(0.0, abs=1e-12)


def test_ordering_is_by_score_not_by_input_order():
    unsorted = rows([(1, -3), (4, 10), (2, 0), (3, 5)])
    assert qini_from_rows(unsorted).cumulative == pytest.approx([0, 10, 15, 15, 12])


def test_oracle_ranking_dominates_any_other_ranking():
    """Ranking by realised uplift is the achievable ceiling. No score ordering
    can beat it - that is what makes it a ceiling rather than a result."""
    data = [(4, 2), (3, 10), (2, -4), (1, 6)]
    model = qini_from_rows(rows(data), key="score")
    oracle = qini_from_rows(rows(data), key="uplift_rupees")
    assert qini_coefficient(oracle) >= qini_coefficient(model)
    assert oracle.peak_value >= model.peak_value


# -- degenerate inputs -------------------------------------------------------


def test_all_zero_uplift_does_not_divide_by_zero():
    c = qini_from_rows(rows([(3, 0), (2, 0), (1, 0)]))
    assert c.final_value == 0
    assert qini_coefficient(c) == 0.0


def test_net_zero_uplift_does_not_divide_by_zero():
    """Gains and losses cancel exactly. The coefficient is undefined here, and
    returning 0.0 is the documented choice - the point is that it does not
    raise mid-run."""
    c = qini_from_rows(rows([(2, 7), (1, -7)]))
    assert c.final_value == pytest.approx(0)
    assert qini_coefficient(c) == 0.0


def test_empty_input_returns_a_degenerate_curve():
    c = qini_from_rows([])
    assert isinstance(c, QiniCurve)
    assert c.final_value == 0.0
    assert qini_coefficient(c) == 0.0


def test_single_row():
    c = qini_from_rows(rows([(1, 9)]))
    assert c.cumulative == pytest.approx([0, 9])
    assert c.peak_fraction == pytest.approx(1.0)


def test_every_case_a_sleeping_dog_peaks_at_zero():
    """If contacting anyone loses money, the correct fraction to target is
    none of them, and the peak has to land at 0%."""
    c = qini_from_rows(rows([(3, -1), (2, -5), (1, -2)]))
    assert c.peak_fraction == 0.0
    assert c.peak_value == 0.0
    assert c.final_value == pytest.approx(-8)


# -- the knee ----------------------------------------------------------------
#
# `peak_fraction` finds where the curve turns DOWN. On a curve that never turns
# down it lands at 100% and reports a cost-of-stopping-late of zero - correct
# arithmetic, useless advice. The knee answers the question actually being
# asked: where do the remaining cases stop paying for their contacts?

def test_knee_finds_the_elbow_on_a_monotone_curve():
    """Front-loaded gains, then a long flat tail. The peak is at 100% and says
    nothing; the knee should land where the curve flattens."""
    rows_ = rows([(10, 100), (9, 100), (8, 100), (7, 1), (6, 1), (5, 1),
                  (4, 1), (3, 1), (2, 1), (1, 1)])
    c = qini_from_rows(rows_)
    assert c.peak_fraction == pytest.approx(1.0)      # never turns down
    assert c.value_left_on_table == pytest.approx(0)  # so this says nothing
    knee_fraction, _ = c.knee
    assert 0.2 <= knee_fraction <= 0.4                # but the elbow is real


def test_knee_of_a_straight_line_is_degenerate():
    """A curve with no elbow should not invent one. Constant uplift means the
    curve IS the chord, so no point rises above it."""
    c = qini_from_rows(rows([(4, 5), (3, 5), (2, 5), (1, 5)]))
    assert c.knee[0] == 0.0


def test_fraction_for_reads_off_the_share():
    rows_ = rows([(4, 80), (3, 10), (2, 5), (1, 5)])
    c = qini_from_rows(rows_)
    assert c.fraction_for(0.8) == pytest.approx(0.25)   # first case alone
    assert c.fraction_for(1.0) == pytest.approx(1.0)


def test_fraction_for_handles_a_worthless_ranking():
    c = qini_from_rows(rows([(2, -5), (1, -5)]))
    assert c.fraction_for(0.8) == 1.0  # no division by a negative total
