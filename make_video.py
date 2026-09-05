"""Render the pitch video: slides + narration -> chukta_pitch.mp4

    python make_video.py

Built because the deadline landed during exam week. It is a slide deck with
synthesised narration, not a screen recording - so it is reproducible, and the
wording can be edited here and re-rendered in about a minute rather than
re-recorded.

Pipeline:
    SLIDES -> PNG via Pillow
           -> WAV per slide via Windows SAPI (PowerShell)
           -> concatenated to MP4 via ffmpeg, each slide held for exactly as
              long as its own narration

Every figure quoted in the narration is asserted in CI by eval/check_claims.py,
so the video cannot drift from the code without the build going red.

Honest limitation: SAPI voices are synthetic and sound it. A phone recording of
a human reading the same script would be better. This exists because a complete
submission beats an incomplete one.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path("video")
W, H = 1920, 1080

# Same palette as the dashboard, which is tuned for screen capture.
BG = (14, 20, 22)
PANEL = (20, 28, 31)
INK = (219, 230, 228)
DIM = (143, 163, 163)
FAINT = (95, 115, 117)
ACCENT = (84, 183, 165)
BLOCK = (212, 102, 92)
PASS = (78, 157, 110)

FONTS = [
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibri.ttf",
]
MONO = [
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/cour.ttf",
]


# Segoe UI has no Devanagari glyphs, so चुकता rendered as five tofu boxes on the
# title slide. Nirmala UI ships with Windows and does have them.
DEVANAGARI = [
    "C:/Windows/Fonts/Nirmala.ttf",
    "C:/Windows/Fonts/mangal.ttf",
]


def font(size: int, mono: bool = False, devanagari: bool = False):
    families = DEVANAGARI if devanagari else (MONO if mono else FONTS)
    for path in families:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def has_devanagari(text: str) -> bool:
    return any("\u0900" <= ch <= "\u097F" for ch in text)


@dataclass
class Slide:
    kicker: str
    title: str
    narration: str
    bullets: list[str] = field(default_factory=list)
    table: list[tuple[str, str]] = field(default_factory=list)
    highlight: str = ""
    footer: str = ""


# --------------------------------------------------------------------------
# The script. Target is under 5 minutes at SAPI default rate.
# --------------------------------------------------------------------------

SLIDES: list[Slide] = [
    Slide(
        kicker="RAZORPAY AI BUILDATHON  ·  TRACK 03",
        title="Chukta",
        highlight="A recovery agent that knows when to stop",
        # Devanagari needs complex-text shaping (conjuncts), which Pillow
        # cannot do without libraqm - it rendered as tofu. The narration
        # says the word anyway, so the slide uses the transliteration.
        footer="Chukta - Hindi for settled, or paid up",
        narration=(
            "This is Chukta. It works failed Razorpay payments. "
            "The name is Hindi for settled, or paid up — it's named for the "
            "outcome, not for the act of collecting, and that turns out to be "
            "the whole idea."
        ),
    ),
    Slide(
        kicker="THE PROBLEM",
        title="The default retry ladder is wrong twice",
        bullets=[
            "Retry at T+1h, T+24h, T+72h. Same card. Generic SMS.",
            "It ignores WHY it failed — an expired card retried three times",
            "gives three guaranteed declines and a worse issuer decline ratio.",
            "It ignores what trying COSTS — messaging a lukewarm subscriber",
            "can cause the cancellation you were trying to prevent.",
        ],
        narration=(
            "A payment fails. In subscriptions that's usually a cancelled "
            "customer, not one missed charge. "
            "The default fix everywhere is a fixed retry ladder — same card, "
            "generic text message. It's wrong in two ways. "
            "It ignores why the payment failed. Retry an expired card three times "
            "and you get three guaranteed declines and a worse decline ratio with "
            "the issuer, which raises your cost on every future transaction. "
            "And it ignores what trying costs. Outreach to a lukewarm subscriber "
            "can trigger the exact cancellation you were trying to prevent."
        ),
    ),
    Slide(
        kicker="THE PART THAT MATTERS",
        title="One run. Five headlines.",
        table=[
            ("Recovery rate, pursued cases only", "60.6%"),
            ("Gross recovery rate, all cases", "54.7%"),
            ("Total rupees recovered", "Rs 195,704"),
            ("Attributable to an action", "Rs 134,177"),
            ("INCREMENTAL vs control arm", "Rs 37,667"),
        ],
        highlight="68 of 164 recoveries were customers who paid anyway",
        narration=(
            "Before I show you what I built, here's the number I refuse to lead "
            "with. "
            "This is one run. One policy. One population. The only thing that "
            "changes between these five rows is the framing. "
            "Sixty point six percent recovery rate. Fifty four percent. One hundred "
            "and ninety five thousand rupees. All true. All from that same run. "
            "The honest number is thirty seven thousand — five times smaller — "
            "because sixty eight of a hundred and sixty four so-called recoveries "
            "were customers who paid without the agent doing anything at all. "
            "Every framing except the last counts those people as a win. "
            "That's not a hypothetical about somebody else's marketing. It's my own "
            "data, and it's why everything after this slide is incremental."
        ),
    ),
    Slide(
        kicker="HOW IT WORKS",
        title="Specify, verify, enforce, trace",
        bullets=[
            "DIAGNOSE   source x step x reason  ->  recoverability class",
            "           109 verified rules, two tiers, confidence recorded",
            "DECIDE     class  ->  intervention, rail, schedule",
            "VERIFY     RBI and TRAI checked BEFORE execution, never after",
            "TRACE      one hash-chained audit row per decision",
        ],
        highlight="Five stopping rules. Nothing else ends a case.",
        narration=(
            "Here's the pipeline. "
            "Razorpay ships an error triplet on every failed payment — a source, a "
            "step, and a reason. Chukta maps that to one of eight recoverability "
            "classes using a hundred and nine rules verified against Razorpay's own "
            "documentation. It's two-tiered, so an unrecognised reason degrades to "
            "a coarser class instead of falling out of the system. "
            "Then it picks an intervention matched to the cause. An expired card "
            "gets an instrument-update request, never another charge. Insufficient "
            "funds waits for salary day. "
            "Every action passes compliance gates before it executes — R B I "
            "e-mandate rules and T R A I messaging rules — and every gate runs, "
            "because the audit row should show the complete verdict rather than "
            "the first objection."
        ),
    ),
    Slide(
        kicker="RESULTS",
        title="+Rs 16,653 mean, and what it costs",
        table=[
            ("12-seed mean incremental", "+Rs 16,653"),
            ("95% confidence interval", "[9,274 , 24,032]"),
            ("Seeds positive", "11 of 12"),
            ("Extra customer contacts", "+55"),
            ("Extra cancellations", "+1.7"),
        ],
        highlight="One seed comes out negative. That is reported, not hidden.",
        narration=(
            "Here are the results with the costs attached. "
            "Across twelve seeds, plus sixteen thousand six hundred rupees mean "
            "incremental revenue, ninety five percent interval nine to twenty four "
            "thousand, positive in eleven of twelve seeds. One seed comes out "
            "negative, and that's reported rather than hidden. It costs fifty five "
            "extra contacts and one point seven extra cancellations. "
            "An earlier version reported a much bigger number from a single seed. "
            "That seed turned out to be the best of twelve — it read like a "
            "measurement and it was an anecdote."
        ),
    ),
    Slide(
        kicker="WHERE IT BREAKS",
        title="One assumption carries the whole result",
        table=[
            ("baseline", "16,653      11/12"),
            ("outreach works better", "22,138      12/12"),
            ("customers churn twice as fast", "17,614      11/12"),
            ("retry timing matters less", "15,406      11/12"),
            ("message frames do nothing", "-8,129       3/12"),
        ],
        highlight="If the copy carries no lift, this agent is NET NEGATIVE",
        narration=(
            "Now the part most demos skip. "
            "Every number comes from a simulator I wrote, so I perturb one belief at "
            "a time and re-run all twelve seeds. Six scenarios survive. One does "
            "not. "
            "If the behavioural message frames carry no lift in payments, this agent "
            "is net negative. Minus eight thousand rupees, positive in only three of "
            "twelve seeds. "
            "Those frames come from tax compliance trials in Guatemala, where the "
            "ask and the consequences are completely different. Nothing I built "
            "tests whether they transfer. "
            "That's the most important finding here, and the one that makes the "
            "project look worst."
        ),
    ),
    Slide(
        kicker="HONEST ACCOUNTING",
        title="Most of the gain is not mine",
        bullets=[
            "blind ladder      ->  smart timing         -4,443   commodity",
            "smart timing      ->  reason-aware        +27,515   commodity",
            "reason-aware      ->  Chukta               -6,418   this project",
            "",
            "My contribution gives up revenue to buy",
            "49% fewer contacts and 43% less churn.",
        ],
        highlight="Pays off only if a retained customer is worth > Rs 2,201",
        narration=(
            "And here's the accounting I'd least like to show. "
            "I ran the competing strategies through the identical harness. Smart "
            "retry timing alone is actually negative here. Reason-aware diagnosis "
            "contributes twenty seven thousand — and that's commodity, every serious "
            "vendor ships it. "
            "My own contribution is minus six thousand four hundred rupees. It gives "
            "revenue up to buy forty nine percent fewer contacts and forty three "
            "percent less churn. "
            "That only pays off if a retained customer is worth more than two "
            "thousand two hundred rupees in future billing. I have no retention "
            "data, so I'm not claiming it."
        ),
    ),
    Slide(
        kicker="WHY BELIEVE ANY OF IT",
        title="Every number is checkable",
        bullets=[
            "296 tests. Qini validated against Hillstrom's 1998 randomised trial,",
            "not just against my own simulator.",
            "Doubly robust off-policy estimate lands within 5% of ground truth.",
            "eval/check_claims.py turns every README figure into a CI assertion —",
            "if the code stops matching the claims, the build goes red.",
        ],
        highlight="github.com/AnshulPatil2005/chukta",
        narration=(
            "So why believe any of it. "
            "Two hundred and ninety six tests. The uplift metric is validated "
            "against Hillstrom's published randomised experiment from nineteen "
            "ninety eight — real data nobody here generated — not just against my "
            "own simulator. The off-policy estimator lands within five percent of "
            "ground truth. "
            "And every figure in the README is a continuous integration assertion. "
            "If the code stops matching the claims, the build goes red. That "
            "includes every number in this video. "
            "What would I measure first on real traffic? Not the routing — the copy. "
            "That's Chukta. Thanks for watching."
        ),
    ),
]


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def draw_slide(s: Slide, index: int, total: int) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_kicker = font(30)
    f_title = font(76)
    f_body = font(38, mono=bool(s.bullets))
    f_table = font(40, mono=True)
    f_hi = font(38)
    f_foot = font(28)

    # left accent rule
    d.rectangle([0, 0, 10, H], fill=ACCENT)

    x = 130
    y = 110
    d.text((x, y), s.kicker, font=f_kicker, fill=ACCENT)
    y += 66
    d.text((x, y), s.title, font=f_title, fill=INK)
    y += 128

    if s.bullets:
        for line in s.bullets:
            if line:
                d.text((x, y), line, font=f_body, fill=DIM)
            y += 58

    if s.table:
        panel_h = len(s.table) * 68 + 40
        d.rectangle([x - 30, y - 20, W - 130, y + panel_h], fill=PANEL)
        for i, (label, value) in enumerate(s.table):
            last = i == len(s.table) - 1
            colour = ACCENT if last else DIM
            if value.strip().startswith("-"):
                colour = BLOCK
            d.text((x, y + 10), label, font=f_table, fill=colour)
            vw = d.textlength(value, font=f_table)
            d.text((W - 170 - vw, y + 10), value, font=f_table, fill=colour)
            y += 68
        y += 60

    if s.highlight:
        y = max(y, H - 260)
        d.rectangle([x - 30, y - 18, x - 22, y + 54], fill=ACCENT)
        for i, line in enumerate(textwrap.wrap(s.highlight, 68)):
            d.text((x, y + i * 46), line, font=f_hi, fill=INK)

    if s.footer:
        ff = font(28, devanagari=True) if has_devanagari(s.footer) else f_foot
        d.text((x, H - 110), s.footer, font=ff, fill=FAINT)

    prog = f"{index + 1} / {total}"
    pw = d.textlength(prog, font=f_foot)
    d.text((W - 130 - pw, H - 110), prog, font=f_foot, fill=FAINT)
    return img


def narrate(text: str, path: Path, rate: int = 0) -> None:
    """Windows SAPI. Zira is clearer than David for technical content."""
    escaped = text.replace("'", "''")
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "try { $s.SelectVoice('Microsoft Zira Desktop') } catch {}; "
        f"$s.Rate = {rate}; "
        f"$s.SetOutputToWaveFile('{path.resolve()}'); "
        f"$s.Speak('{escaped}'); "
        "$s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   check=True, capture_output=True)


def duration(path: Path, ffprobe: str) -> float:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def main() -> int:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    # imageio ships ffmpeg but not ffprobe; ffmpeg can report duration too.
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    print(f"rendering {len(SLIDES)} slides")
    durations = []
    for i, s in enumerate(SLIDES):
        png = OUT / f"slide_{i:02d}.png"
        wav = OUT / f"audio_{i:02d}.wav"
        draw_slide(s, i, len(SLIDES)).save(png)
        narrate(s.narration, wav)

        # ffmpeg -i on a wav prints duration to stderr; parse it rather than
        # shipping a second binary just to ask how long a file is.
        info = subprocess.run([ffmpeg, "-i", str(wav)],
                              capture_output=True, text=True).stderr
        stamp = [ln for ln in info.splitlines() if "Duration:" in ln][0]
        hh, mm, ss = stamp.split("Duration:")[1].split(",")[0].strip().split(":")
        secs = int(hh) * 3600 + int(mm) * 60 + float(ss)
        durations.append(secs)
        print(f"  {i + 1}/{len(SLIDES)}  {secs:5.1f}s  {s.title[:44]}")

    total = sum(durations)
    print(f"\ntotal narration: {total / 60:.1f} min")

    # Concat list: each image held for exactly its narration length.
    concat = OUT / "slides.txt"
    with open(concat, "w", encoding="utf-8") as fh:
        for i, secs in enumerate(durations):
            fh.write(f"file 'slide_{i:02d}.png'\nduration {secs:.3f}\n")
        # ffmpeg's concat demuxer drops the final entry without a repeat.
        fh.write(f"file 'slide_{len(durations) - 1:02d}.png'\n")

    audio_list = OUT / "audio.txt"
    with open(audio_list, "w", encoding="utf-8") as fh:
        for i in range(len(durations)):
            fh.write(f"file 'audio_{i:02d}.wav'\n")

    print("encoding...")
    subprocess.run(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-f", "concat", "-safe", "0", "-i", str(audio_list),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25",
         "-c:a", "aac", "-b:a", "192k",
         # The concat demuxer needs the last image repeated or it drops the
         # final entry, which otherwise leaves a silent tail. Cut to audio.
         "-t", f"{total:.3f}",
         "chukta_pitch.mp4"],
        check=True, capture_output=True,
    )

    size = Path("chukta_pitch.mp4").stat().st_size / 1e6
    print(f"\n  -> chukta_pitch.mp4  ({size:.1f} MB, {total / 60:.1f} min)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
