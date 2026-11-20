"""Data-quality checks for daily series: flag what needs review, score it, and say what training does about it.

Built for a public source that publishes *unvalidated* readings (reservoir storage), reusable for any daily
series with a level and/or a percentage and/or a volume. Per series and per day it finds:

- `duplicate`  more than one reading for the same day;
- gaps         missing days (reported as ranges, not as flags on rows that do not exist);
- `flatline`   the same reading for N or more consecutive days;
- `jump`       a change of more than X percentage points from the previous day;
- `pct_range`  a percentage outside [pct_min, pct_max];
- `level_volume` a volume far from what a monotonic level -> volume curve, fitted on the series itself, expects.

The tone is deliberate: a flag says **"needs review"**, never "wrong". A reservoir above 100 % may be spilling;
a flat line may be a quiet week. Nothing is deleted from the data: `cleaned` applies a documented policy to a
*copy* for training, and the original readings stay visible next to their flags.

Pure Python (libs/shared has no numpy). The curve is isotonic regression (pool-adjacent-violators) on quantile bins.
"""

from __future__ import annotations

import statistics
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from itertools import pairwise

from pydantic import BaseModel, ConfigDict, Field

from ai_circus_shared.open_data import Observation

DUPLICATE = "duplicate"
FLATLINE = "flatline"
JUMP = "jump"
PCT_RANGE = "pct_range"
LEVEL_VOLUME = "level_volume"
GAP = "gap"

DESCRIPTIONS: dict[str, str] = {
    DUPLICATE: "needs review: more than one reading was published for this day",
    FLATLINE: "needs review: the reading did not change for several days in a row",
    JUMP: "needs review: the percentage moved by more than is usual in one day",
    PCT_RANGE: "needs review: the percentage is outside the 0-100 range (it may be spilling, or a reading issue)",
    LEVEL_VOLUME: "needs review: the volume does not fit the level-to-volume curve of this series",
    GAP: "needs review: no reading was published for these days",
}


class QualityConfig(BaseModel):
    """Thresholds and the training policy. Every number is configuration, none is hard-wired."""

    model_config = ConfigDict(extra="forbid")

    flatline_days: int = Field(default=14, ge=2)
    max_daily_jump_pct: float = Field(default=5.0, gt=0)
    pct_min: float = 0.0
    pct_max: float = 100.0
    curve_bins: int = Field(default=60, ge=5)
    curve_min_points: int = Field(default=200, ge=10)
    curve_window_days: int | None = Field(default=None, ge=30)
    curve_mad_k: float = Field(default=8.0, gt=0)
    curve_min_abs_residual: float = Field(default=0.0, ge=0)  # in volume units: ignore a smaller residual
    # Training policy: these flags drop the reading from the training copy; a gap of up to
    # `max_impute_gap_days` is then filled by linear interpolation, a longer one stays a gap.
    exclude_flags: list[str] = Field(default_factory=lambda: [JUMP, LEVEL_VOLUME])
    max_impute_gap_days: int = Field(default=3, ge=0)


class Flag(BaseModel):
    model_config = ConfigDict(frozen=True)

    series: str
    day: date
    code: str
    detail: str = ""


class Gap(BaseModel):
    model_config = ConfigDict(frozen=True)

    series: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


class QualityReport(BaseModel):
    flags: list[Flag] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict)  # per series, 0-100, share of days not needing review

    def counts(self) -> dict[str, int]:
        counts = Counter(f.code for f in self.flags)
        if self.gaps:
            counts[GAP] = sum(g.days for g in self.gaps)
        return dict(counts)

    def for_series(self, series: str, start: date | None = None, end: date | None = None) -> list[Flag]:
        return [
            f
            for f in self.flags
            if f.series == series and (start is None or f.day >= start) and (end is None or f.day <= end)
        ]


def _isotonic(values: Sequence[float], weights: Sequence[float]) -> list[float]:
    """Non-decreasing fit of `values` (pool adjacent violators)."""
    blocks: list[list[float]] = []  # [mean, weight, count]
    for v, w in zip(values, weights, strict=True):
        blocks.append([v, w, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            (m2, w2, c2), (m1, w1, c1) = blocks[-1], blocks[-2]
            blocks[-2:] = [[(m1 * w1 + m2 * w2) / (w1 + w2), w1 + w2, c1 + c2]]
    fit: list[float] = []
    for mean, _, count in blocks:
        fit.extend([mean] * int(count))
    return fit


def _level_volume_curve(points: Sequence[tuple[float, float]], bins: int):
    """A monotonic level -> volume function from (level, volume) pairs: quantile-bin medians, then isotonic."""
    ordered = sorted(points)
    size = max(1, len(ordered) // bins)
    xs: list[float] = []
    ys: list[float] = []
    ws: list[float] = []
    for i in range(0, len(ordered), size):
        chunk = ordered[i : i + size]
        xs.append(statistics.median(p[0] for p in chunk))
        ys.append(statistics.median(p[1] for p in chunk))
        ws.append(float(len(chunk)))
    fitted = _isotonic(ys, ws)

    def curve(level: float) -> float:
        if len(xs) < 2:
            return fitted[0]
        # Beyond the outermost bin medians, carry on along the last segment (never downhill): clamping flat
        # would flag every honest reading at the extremes of a steep curve.
        if level <= xs[0] or level >= xs[-1]:
            i = 1 if level <= xs[0] else len(xs) - 1
            slope = max(0.0, (fitted[i] - fitted[i - 1]) / (xs[i] - xs[i - 1])) if xs[i] != xs[i - 1] else 0.0
            anchor = 0 if level <= xs[0] else len(xs) - 1
            return fitted[anchor] + slope * (level - xs[anchor])
        i = bisect_left(xs, level)
        x0, x1, y0, y1 = xs[i - 1], xs[i], fitted[i - 1], fitted[i]
        return y0 if x1 == x0 else y0 + (y1 - y0) * (level - x0) / (x1 - x0)

    return curve


def _check_one(series: str, rows: list[Observation], config: QualityConfig) -> tuple[list[Flag], list[Gap], float]:
    flags: list[Flag] = []
    by_day: dict[date, list[Observation]] = defaultdict(list)
    for row in rows:
        by_day[row.day].append(row)
    for day, same in sorted(by_day.items()):
        if len(same) > 1:
            flags.append(Flag(series=series, day=day, code=DUPLICATE, detail=f"{len(same)} readings"))
    days = sorted(by_day)
    first = {d: by_day[d][0] for d in days}  # duplicates: the first published reading is the one checked

    gaps = [
        Gap(series=series, start=a + timedelta(days=1), end=b - timedelta(days=1))
        for a, b in pairwise(days)
        if (b - a).days > 1
    ]

    for row in first.values():
        if row.pct is not None and not config.pct_min <= row.pct <= config.pct_max:
            flags.append(Flag(series=series, day=row.day, code=PCT_RANGE, detail=f"{row.pct:g} %"))

    for prev_day, day in pairwise(days):
        a, b = first[prev_day], first[day]
        if (day - prev_day).days == 1 and a.pct is not None and b.pct is not None:
            step = abs(b.pct - a.pct)
            if step > config.max_daily_jump_pct:
                flags.append(Flag(series=series, day=day, code=JUMP, detail=f"{step:.1f} percentage points in a day"))

    run: list[date] = []
    last_key: tuple[float | None, float | None] | None = None

    def close_run() -> None:
        if len(run) >= config.flatline_days:
            flags.extend(Flag(series=series, day=d, code=FLATLINE, detail=f"{len(run)} days unchanged") for d in run)

    for day in days:
        row = first[day]
        key = (row.pct, row.volume)
        contiguous = bool(run) and (day - run[-1]).days == 1
        if key != (None, None) and key == last_key and contiguous:
            run.append(day)
        else:
            close_run()
            run = [day]
            last_key = key
    close_run()

    # The curve is fitted per block of `curve_window_days` (the whole series if None): a reservoir's
    # level -> volume table is revised from time to time, and one curve over a revision would flag months of
    # honest readings. Inside a block a reading is only compared with its neighbours' curve.
    blocks: dict[int, list[Observation]] = defaultdict(list)
    for r in first.values():
        if r.level is not None and r.volume is not None:
            blocks[(r.day - days[0]).days // config.curve_window_days if config.curve_window_days else 0].append(r)
    for rows_in_block in blocks.values():
        if len(rows_in_block) < config.curve_min_points:
            continue
        curve = _level_volume_curve([(r.level, r.volume) for r in rows_in_block], config.curve_bins)  # type: ignore[misc]
        residuals = {r.day: r.volume - curve(r.level) for r in rows_in_block}  # type: ignore[operator]
        centre = statistics.median(residuals.values())
        scale = 1.4826 * statistics.median(abs(v - centre) for v in residuals.values())
        limit = max(config.curve_mad_k * scale, config.curve_min_abs_residual)
        for day, res in residuals.items():
            if abs(res - centre) > limit > 0:
                detail = f"{res - centre:+.2f} from the curve"
                flags.append(Flag(series=series, day=day, code=LEVEL_VOLUME, detail=detail))

    expected = (days[-1] - days[0]).days + 1 if days else 0
    problem = {f.day for f in flags} | {g.start + timedelta(days=i) for g in gaps for i in range(g.days)}
    score = 100.0 * (1 - len(problem) / expected) if expected else 100.0
    return flags, gaps, round(score, 2)


def check(observations: Iterable[Observation], config: QualityConfig | None = None) -> QualityReport:
    """Run every check on every series. Deterministic: flags come out ordered by series, then day, then code."""
    config = config or QualityConfig()
    grouped: dict[str, list[Observation]] = defaultdict(list)
    for obs in observations:
        grouped[obs.series].append(obs)
    report = QualityReport()
    for series in sorted(grouped):
        flags, gaps, score = _check_one(series, grouped[series], config)
        report.flags.extend(sorted(flags, key=lambda f: (f.day, f.code)))
        report.gaps.extend(gaps)
        report.scores[series] = score
    return report


def score_for(report: QualityReport, series: str, start: date, end: date) -> float:
    """Share of the days in [start, end] with no flag and no gap, 0-100."""
    total = (end - start).days + 1
    if total <= 0:
        return 100.0
    bad = {f.day for f in report.for_series(series, start, end)}
    for g in report.gaps:
        if g.series == series:
            bad |= {g.start + timedelta(days=i) for i in range(g.days) if start <= g.start + timedelta(days=i) <= end}
    return round(100.0 * (1 - len(bad) / total), 2)


def cleaned(
    observations: Iterable[Observation], report: QualityReport, config: QualityConfig | None = None
) -> tuple[list[Observation], dict[str, int]]:
    """The training copy: duplicates reduced to the first reading, readings carrying an `exclude_flags` flag
    dropped, and short gaps (originals and those just made) filled by linear interpolation. Returns the copy
    and a count of what was done, so the rule that was applied can be stated next to the result."""
    config = config or QualityConfig()
    excluded = {(f.series, f.day) for f in report.flags if f.code in config.exclude_flags}
    kept: dict[str, dict[date, Observation]] = defaultdict(dict)
    notes = {"duplicates_dropped": 0, "excluded": 0, "imputed_days": 0}
    for obs in observations:
        if obs.day in kept[obs.series]:
            notes["duplicates_dropped"] += 1
        elif (obs.series, obs.day) in excluded:
            notes["excluded"] += 1
        else:
            kept[obs.series][obs.day] = obs
    out: list[Observation] = []
    for series in sorted(kept):
        days = sorted(kept[series])
        for i, day in enumerate(days):
            out.append(kept[series][day])
            if i + 1 == len(days):
                break
            nxt = days[i + 1]
            hole = (nxt - day).days - 1
            if 0 < hole <= config.max_impute_gap_days:
                a, b = kept[series][day], kept[series][nxt]
                for k in range(1, hole + 1):
                    t = k / (hole + 1)
                    out.append(
                        Observation(
                            series=series,
                            day=day + timedelta(days=k),
                            **{
                                name: (
                                    None
                                    if getattr(a, name) is None or getattr(b, name) is None
                                    else getattr(a, name) + (getattr(b, name) - getattr(a, name)) * t
                                )
                                for name in ("level", "pct", "volume")
                            },
                        )
                    )
                    notes["imputed_days"] += 1
    return out, notes
