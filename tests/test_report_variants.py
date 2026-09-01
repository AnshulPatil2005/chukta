"""The five-headline comparison.

This exists to make one claim checkable: the same run, reported the way the
industry reports recovery numbers, looks several times better than it is. If
that spread ever collapses, either the simulator stopped modelling
self-recovery or the framings stopped differing - and either way the argument
the README makes would no longer be supported by its own data.
"""

from __future__ import annotations

import pytest

from eval.metrics import load_run
from eval.report_variants import variants


@pytest.fixture(scope="module")
def run():
    return load_run()


def test_exactly_one_framing_is_marked_honest(run):
    honest = [v for v in variants(run) if v.honest]
    assert len(honest) == 1
    assert "INCREMENTAL" in honest[0].label


def test_the_honest_number_is_the_smallest_rupee_figure(run):
    """If the incremental figure were ever the largest, the whole argument
    would be backwards and the README would be quoting the flattering one."""
    rupee = [v for v in variants(run) if v.value.startswith("Rs ")]
    amounts = [(v.label, float(v.value.replace("Rs ", "").replace(",", "")))
               for v in rupee]
    honest = next(a for label, a in amounts if "INCREMENTAL" in label)
    assert honest == min(a for _, a in amounts), amounts


def test_the_flattering_framing_is_at_least_three_times_the_honest_one(run):
    """The spread IS the finding. A small spread would mean self-recovery is
    negligible on this population, which would undercut the thesis."""
    rupee = {v.label: float(v.value.replace("Rs ", "").replace(",", ""))
             for v in variants(run) if v.value.startswith("Rs ")}
    gross = rupee["Total rupees recovered"]
    honest = rupee["INCREMENTAL vs control arm"]
    assert gross / honest >= 3.0, f"spread collapsed to {gross / honest:.1f}x"


def test_pursued_only_beats_gross_rate(run):
    """Dropping declined cases from the denominator has to raise the number -
    that is precisely why it is the most flattering framing."""
    v = {x.label: x.value for x in variants(run)}
    pursued = float(v["Recovery rate, pursued cases only"].rstrip("%"))
    gross = float(v["Gross recovery rate, all cases"].rstrip("%"))
    assert pursued > gross


def test_a_large_share_of_recoveries_are_self_recoveries(run):
    """The mechanism behind the spread. If this ever drops near zero the
    simulator has stopped modelling customers who pay unaided, and every
    incremental claim in the repo would need re-examining."""
    treated = run["arms"]["chukta"]
    recovered = [c for c in treated if c["recovered"]]
    by_self = [c for c in recovered if c["recovered_by"] == "self"]
    assert 0.25 <= len(by_self) / len(recovered) <= 0.75


def test_every_framing_carries_an_explanation(run):
    """A number without its caveat is how this goes wrong in the first place."""
    for v in variants(run):
        assert len(v.why) > 60, v.label
