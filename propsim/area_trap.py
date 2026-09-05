#!/usr/bin/env python3
"""AreaTrap pattern core + PropSim plugin.

Faithful port of `ninjascript/AreaTrapCore.cs` (`AtEngine`) -- the failed break
of a rolling session volume-profile value area. Parameter names are the NT8
property names in snake_case and the list is CLOSED, mirroring the LatigoBreak
/ PullbackZone rule: an extra dial here would make the PropSim run and the
Market Replay run two different experiments. Defaults are `AreaTrapStrategy.cs`
`SetDefaults` values verbatim -- every filter OFF, matching the note there that
they are "the experiment, not a filter someone already validated".

Sandbox: the loader injects `np` (and `tp`/`Strategy`/`Param`) rather than
letting a plugin import anything, but numpy imports are allowed anyway (see
`pullback_zone.py`'s note, same PropSim commit). The import exists so this
file runs its own selfchecks standalone.

FIDELITY -- every delta from AreaTrapCore.cs:

  1. ENTRY MODE. Only `EntryOnBarClose = True` (the C# default, and the one
     the video and the mockup describe) is ported. `AtEntryMode.ReclaimTick`
     (a resting stop order at the level, filled the instant it is touched
     rather than waiting for the bar to close) is NOT implemented: the C#
     header itself calls BarClose "a convention, not a finding" and says
     ReclaimTick exists to be measured against it via the telemetry table,
     which this port does not reproduce. `entry_on_bar_close` is kept as a
     closed, fixed=True param at 1 so a reader sees the choice was made, not
     forgotten; flipping it to 0 does nothing here.
  2. VOLUME PROFILE INPUT. The core always builds its profile with
     `Profile.AddBarSpread(bar)` -- OHLCV spread evenly across the bar's
     rows -- never the tick-level ladder. The strategy file only uses the
     volumetric ladder for BuyVol/SellVol (the break-volume and absorption
     filters), never for the profile itself. So this port needs exactly what
     `bars` already carries (t/o/h/l/c/v) plus a buy/sell split, and is NOT
     blocked by the tape lacking L1 quotes or DOM -- the one thing AreaTrap's
     decision path actually needs from order flow is the aggressor side per
     print, which every PropSim tape carries (`tape["side"]`).
     CONSEQUENCE: `entries()`'s own `bars` argument is unused -- the window/
     arm elapsed-time math is written for a SPECIFIC 30-second bar, not
     whatever `tf_secs` the engine happens to be running at (its own smoke
     test always calls at 300s), so `_build_bars(tape, TF_SECS)` rebuilds the
     30s bars straight from `tape` instead. Same fix PullbackZone needed
     first; see its own `entries`/`_build_bars` note.
  3. BUY/SELL VOLUME. `tape["side"]` is +1 (at the ask) / -1 (at the bid) / 0
     (unknown); PropSim's own `build_bars` already reduces this to a per-bar
     signed `delta = sum(side * vol)` alongside total volume `v`. This port
     recovers buy = (v + delta) / 2, sell = (v - delta) / 2 -- exact when
     every print classifies, and simply omits unclassified (`side == 0`)
     volume from both sides, the same honest gap NT8's UpDownTick/BidAsk
     ladder has when a print cannot be classified.
  4. FLAT-TO-FLAT GATING. `AtEngine.Phase` stops building a new window the
     instant a trade opens (`InTrade`) and only resumes when NT8 reports flat
     (`OnTradeClosed` -> `StartWindow`). `entries()` cannot know a trade's own
     exit before the tick-fill engine resolves it, so this port re-derives it
     locally (`_resolve_exit`, ported from `pullback_zone.py`'s function of
     the same name and same pessimistic tie-goes-to-the-stop rule) purely to
     decide WHEN the state machine may resume -- the engine's own `resolve()`
     is the sole source of truth for the trade's actual P&L. Bounded to the
     entry's own session (day boundary), same reasoning as PullbackZone.
  5. SESSION / TRADING HOURS. The .cs enforces no time-of-day window; it
     resets its cycle on a bar's calendar-date change
     (`bt.Date != _sessionDate`) and relies on
     `IsExitOnSessionCloseStrategy` to flatten at the instrument's own
     session close. PropSim's `resolve()` already force-flattens every open
     trade at the day boundary (`session_end`) when no `flatten_hhmm` is
     given, which is the same backstop with the same day-boundary
     definition, so `full_session = True` (no RTH filter) and no
     `flatten_hhmm` are used here.
  6. STALE-ARM EXPIRY / MinAreaPoints REJECTION. Ported exactly: an armed
     area older than `arm_minutes` restarts the window with no trade, and a
     frozen area narrower than `min_area_points` (0 = off) is discarded
     before ever arming. Neither produces a row in `entries()`, matching
     `AtAction.None`.
  7. TELEMETRY. `AtTelemetry`'s 8-combination scoreboard (every reclaim
     scored against all 8 filter subsets at once) is NOT reproduced --
     that instrumentation exists to be read from NT8's Output window during
     a live Playback session, and PropSim already answers the same question
     per-run by sweeping `use_declining_volume` / `use_absorption` /
     `use_returning_volume` directly (they are real, if fixed, dials below).
     What IS reproduced is the underlying decision: `required = mask(use_*)`
     against `verdict = mask(passed_*)`, entered only if every requested
     filter passed -- the same rule, just scored one combination per run
     instead of all eight per reclaim.
  8. GAP-THROUGH GUARD. The C#'s BarClose mode submits `EnterLong`/
     `EnterShort` unconditionally once a reclaim passes its filters --
     nothing there checks whether the fill (one tick after the signal bar's
     close) might already be past the very stop/target it is about to be
     given. NT8 would just close such a trade immediately; the engine's own
     invariant (`resolve()`: a trade may not open already past its stop or
     target) would silently drop the same row anyway. This port checks it a
     tick earlier instead (`signals()`'s gap-through guard, ~3% of raw
     reclaims on NQ) so the dropped attempt returns Armed to hunting the
     SAME area rather than opening a phantom instantaneous trade, and so
     `plugins.py --check`'s stricter contract -- no row it returns may ever
     violate that invariant -- passes on real ticks.
"""
import numpy as np

# The PropSim sandbox hands a plugin `Strategy`, `Param` and `np` in its
# namespace instead of letting it import them. Standalone they are simply
# absent, so stand-ins are defined and the class below degrades to a plain
# object. NameError rather than try/except-import (see pullback_zone.py):
# plugins.py's AST check walks the whole tree and rejects an import node
# inside a try/except exactly like a bare one.
try:
    Strategy
except NameError:
    class Strategy:                      # pragma: no cover - sandbox stand-in
        tick = 0.25                      # engine.Strategy.tick default (NQ's)

    class Param:                         # pragma: no cover - sandbox stand-in
        def __init__(self, default, lo, hi, desc, fixed=False):
            self.default, self.lo, self.hi = default, lo, hi
            self.desc, self.fixed = desc, fixed

TICK = 0.25
_TPS = 10_000_000                      # .NET ticks per second
_NET_EPOCH_S = 62135596800             # seconds from 0001-01-01 to 1970-01-01

# AreaTrapStrategy.cs assumes a 30-second bar series throughout (its own
# comments: "the design assumes 30-second bars", "WindowMinutes * 2 bars of
# 30s"). No RTH restriction anywhere in the .cs -- see FIDELITY 5.
TF_SECS = 30
FULL_SESSION = True

# CLOSED PARAMETER LIST, one-to-one with AreaTrapStrategy.cs [NinjaScriptProperty]
# fields. Every value below is the C# `SetDefaults` value verbatim. ALL fixed:
# this project is CERO validated (no Replay run yet) and nothing here has been
# swept, so nothing here should be -- see the trading-strategy-integrator team
# rule against sweeping a dial before its first honest measurement.
PARAMS_DEFAULT = dict(
    window_minutes=10, arm_minutes=10, contracts=1,
    value_area_percent=0.70, ticks_per_level=1, expand_two_rows=0,
    entry_on_bar_close=1,        # see FIDELITY 1 -- the only mode ported
    min_break_points=0.50, max_break_points=0.0,
    allow_long=1, allow_short=1,
    use_declining_volume=0, declining_volume_max=0.80,
    use_absorption=0, absorption_min_bars=2, absorption_min_delta=200,
    use_returning_volume=0, returning_volume_min=1.00,
    stop_buffer_points=1.00, max_stop_points=12.0, min_area_points=0.0,
)


def _day_index(ts):
    return (ts // _TPS - _NET_EPOCH_S) // 86400


def _sec_of_day(ts):
    return (ts // _TPS - _NET_EPOCH_S) % 86400


def _build_bars(tape, secs):
    """Local, tape-only bar builder -- same recipe as `tape.build_bars`
    (bucket by day and time-slot, so no bar spans a session gap). AreaTrap's
    whole state machine (window/arm elapsed-time math, the break/reclaim
    read on 30s granularity) is written for a SPECIFIC bar size, not
    whatever `tf_secs` the caller happens to be running the engine at --
    `plugins.py`'s own smoke test always calls at 300s. `PullbackZone` hit
    this exact mismatch first (see its own `_build_bars`/`entries` note):
    the `bars` argument `entries()` receives is therefore unused here, and
    this rebuilds 30s bars straight from `tape` instead."""
    ts = tape["ts"]
    if not len(ts):
        z = np.array([])
        return dict(t=z, o=z, h=z, l=z, c=z, v=z, delta=z,
                    start=np.array([], np.int64), end=np.array([], np.int64))
    slot = _day_index(ts) * (86400 // secs + 1) + _sec_of_day(ts) // secs
    edge = np.flatnonzero(np.diff(slot)) + 1
    starts = np.concatenate(([0], edge))
    ends = np.concatenate((edge, [len(ts)]))
    px, vol, side = tape["px"], tape["vol"], tape["side"]
    o = px[starts]
    c = px[ends - 1]
    h = np.maximum.reduceat(px, starts)
    l = np.minimum.reduceat(px, starts)
    v = np.add.reduceat(vol, starts)
    delta = np.add.reduceat(side.astype(np.int64) * vol, starts)
    return dict(t=ts[starts], o=o, h=h, l=l, c=c, v=v, delta=delta,
                start=starts, end=ends)


# --------------------------------------------------------------- value area
def _value_area(rows_px, rows_vol, pct, two_rows):
    """AtValueAreaMath.Compute, ported line for line.

    `rows_px`/`rows_vol` are the profile's occupied rows, LOW TO HIGH
    (AtProfile.SortedRows order). Ties expand upward -- an arbitrary rule the
    C# comment defends as the conservative one for a long (it lifts VAL, the
    entry reference, and pulls VAH, the target, closer, so it cannot flatter
    a long result).
    """
    n = len(rows_vol)
    if n == 0:
        return None
    poc_idx = int(np.argmax(rows_vol))
    total = float(rows_vol.sum())
    target = total * pct
    lo = hi = poc_idx
    inside = float(rows_vol[poc_idx])
    step = 2 if two_rows else 1

    while inside < target and (lo > 0 or hi < n - 1):
        up = sum(rows_vol[hi + k] for k in range(1, step + 1) if hi + k < n)
        dn = sum(rows_vol[lo - k] for k in range(1, step + 1) if lo - k >= 0)
        if up >= dn and hi < n - 1:
            for _ in range(step):
                if hi >= n - 1:
                    break
                hi += 1
                inside += rows_vol[hi]
        elif lo > 0:
            for _ in range(step):
                if lo <= 0:
                    break
                lo -= 1
                inside += rows_vol[lo]
        else:
            break

    return dict(poc=float(rows_px[poc_idx]), val=float(rows_px[lo]),
                vah=float(rows_px[hi]), width=float(rows_px[hi] - rows_px[lo]))


class _Profile:
    """AtProfile, ported: sparse volume-at-price built from OHLCV bar spread
    only -- see FIDELITY 2, the core never uses tick-level rows either."""

    def __init__(self, row_size):
        self.row_size = row_size
        self.tot, self.buy, self.sell = {}, {}, {}
        self.total_volume = 0.0

    def row_of(self, px):
        return int(np.floor(px / self.row_size + 1e-9))

    def add_bar_spread(self, o, h, l, c, v, buy, sell):
        if v <= 0:
            return
        lo, hi = self.row_of(l), self.row_of(h)
        if hi < lo:
            lo, hi = hi, lo
        n = hi - lo + 1
        if n <= 0:
            return
        share, rem = v // n, v - (v // n) * n
        # buy_share/sell_share get NO remainder redistribution -- ported
        # exactly from `AtProfile.AddBarSpread`, where `buyShare`/`sellShare`
        # are plain integer division with no `rem` term of their own. A bar
        # whose row count does not divide its buy/sell volume evenly under-
        # counts them by up to (rows - 1) each; total volume is still exact.
        buy_share, sell_share = buy // n, sell // n
        for i, r in enumerate(range(lo, hi + 1)):
            amt = share + (1 if i < rem else 0)
            self.tot[r] = self.tot.get(r, 0) + amt
            if buy_share > 0:
                self.buy[r] = self.buy.get(r, 0) + buy_share
            if sell_share > 0:
                self.sell[r] = self.sell.get(r, 0) + sell_share
            self.total_volume += amt

    def value_area(self, pct, two_rows):
        if self.total_volume <= 0 or not self.tot:
            return None
        rows = sorted(self.tot.keys())
        px = np.array([r * self.row_size for r in rows])
        vol = np.array([self.tot[r] for r in rows], dtype=np.float64)
        return _value_area(px, vol, pct, two_rows)


# -------------------------------------------------------- trade-exit lookup
def _resolve_exit(ts, px, entry_tick, d, stop_px, target_px, session_end_ts):
    """When would this fill's position go flat, on THIS tape? Used only to
    know when the state machine may resume building a new window -- see
    FIDELITY 4. Pessimistic, as everywhere in this house: a tie goes to the
    stop. Bounded by the session end (day boundary), not by any position
    timeout -- AreaTrap has none; the position is managed by its own
    brackets and the session-close flatten alone.
    """
    end = int(np.searchsorted(ts, session_end_ts, "right"))
    seg = px[entry_tick + 1:end]
    if d > 0:
        s = np.flatnonzero(seg <= stop_px + 1e-9)
        t = np.flatnonzero(seg >= target_px - 1e-9)
    else:
        s = np.flatnonzero(seg >= stop_px - 1e-9)
        t = np.flatnonzero(seg <= target_px + 1e-9)
    si = int(s[0]) if len(s) else len(seg)
    ti = int(t[0]) if len(t) else len(seg)
    k = min(si, ti)
    if k == len(seg):
        return int(session_end_ts)
    return int(ts[entry_tick + 1 + k])


_EMPTY4 = (np.array([], np.int64), np.array([], np.int8),
           np.array([]), np.array([]))


# ------------------------------------------------------------ state machine
def signals(bars, tape, p):
    """One pass over closed 30s bars, mirroring AtEngine.OnBar / StepBuilding
    / StepArmed / RaiseCandidate. Returns a list of candidate rows, one per
    RAISED reclaim (`AtAction.EnterLong/Short`) that passed the configured
    filters -- i.e. exactly what NT8 would have submitted an entry for, given
    `_entryInFlight`/flat gating (which the flat-to-flat logic below
    reproduces; see FIDELITY 4).

    A bar's own high/low/close (and the buy/sell split for the bars still
    "since the extreme") are read only for that closed bar -- there is no
    reason to reach past bar `i` for anything the core itself would not have
    had at `Time[1]`.
    """
    t, o, h, l, c, v = (bars["t"], bars["o"], bars["h"], bars["l"], bars["c"],
                        bars["v"])
    delta = bars["delta"]
    n = len(t)
    if n < 2:
        return []
    buy_vol = (v + delta) / 2.0     # FIDELITY 3
    sell_vol = (v - delta) / 2.0
    close_t = t + TF_SECS * _TPS     # NT8's Time[1] is the bar's CLOSE, not open
    day = _day_index(close_t)

    row_size = TICK * max(1, int(p["ticks_per_level"]))
    va_pct = float(p["value_area_percent"])
    two_rows = bool(p["expand_two_rows"])
    window_min = max(1, int(p["window_minutes"]))
    arm_min = max(1, int(p["arm_minutes"]))
    min_break = float(p["min_break_points"])
    max_break = float(p["max_break_points"])
    stop_buf = float(p["stop_buffer_points"])
    max_stop = float(p["max_stop_points"])
    min_area = float(p["min_area_points"])
    allow_long, allow_short = bool(p["allow_long"]), bool(p["allow_short"])
    req_mask = ((1 if p["use_declining_volume"] else 0)
                | (2 if p["use_absorption"] else 0)
                | (4 if p["use_returning_volume"] else 0))
    decl_max = float(p["declining_volume_max"])
    abs_min_bars = int(p["absorption_min_bars"])
    abs_min_delta = float(p["absorption_min_delta"])
    ret_min = float(p["returning_volume_min"])

    out = []
    ts, px = tape["ts"], tape["px"]

    # A plain attribute-mutation state holder, NOT `nonlocal`/`global` closure
    # variables -- plugins.py's AST check bans both statements outright ("a
    # strategy keeps no state between runs"), and correctly so at module
    # scope, but a bar-by-bar state machine still has to carry its state
    # somewhere for the DURATION OF ONE CALL. An object's attributes are not
    # a `nonlocal` rebinding and are not module state: `s` is created fresh
    # on every `signals()` call and discarded when it returns.
    s = _St(row_size, close_t[0])
    s.prev_day = int(day[0])

    for i in range(n):
        bt = int(close_t[i])

        # While InTrade, the core does nothing at all until NT8 reports flat
        # (FIDELITY 4) -- the state machine simply does not see these bars.
        if s.phase == 2:
            if bt < s.busy_until:
                continue
            _restart(s, bt, row_size)

        # New calendar day: nothing frozen carries across the close (matches
        # `bt.Date != _sessionDate`, see FIDELITY 5).
        d_i = int(day[i])
        if d_i != s.prev_day:
            s.prev_day = d_i
            _restart(s, bt, row_size)

        # Up to TWO phase passes per bar, exactly like `AtEngine.OnBar`: the
        # window can close and the hunt begin on the same bar, or a stale arm
        # can expire and the next window open on the same bar. One pass is
        # not enough -- see that method's own comment for the one-bar
        # lookahead a single pass would hide as an off-by-one.
        raised = None
        for _pass in range(2):
            phase_before = s.phase
            if s.phase == 0:
                _step_building(s, i, bt, o, h, l, c, v, buy_vol, sell_vol,
                                row_size, window_min, min_area, va_pct,
                                two_rows)
            elif s.phase == 1:
                raised = _step_armed(
                    s, i, bt, o, h, l, c, v, buy_vol, sell_vol, row_size,
                    arm_min, min_break, max_break, allow_long, allow_short,
                    decl_max, abs_min_bars, abs_min_delta, ret_min,
                    req_mask, stop_buf, max_stop, n, bars, ts, px)
            else:
                break             # InTrade: the strategy owns the exit
            if s.phase == phase_before:
                break
        if raised is not None:
            out.append(raised)

    return out


class _St:
    """Mutable cycle state for one `signals()` call -- see the note at its
    call site on why this is attribute mutation and not `nonlocal`."""

    def __init__(self, row_size, t0):
        self.phase = 0            # 0 Building, 1 Armed, 2 InTrade
        self.profile = _Profile(row_size)
        self.build_vols = []
        self.build_median = 0.0
        self.window_start = t0
        self.arm_start = 0
        self.area = None
        self.broke = False
        self.break_side = 0
        self.break_extreme = 0.0
        self.break_bars = 0
        self.break_vol_sum = 0.0
        self.bars_since_extreme = 0
        self.delta_since_extreme = 0.0
        self.prev_day = 0
        self.busy_until = -1      # no new window while a bar's close < this


def _restart(s, t0, row_size):
    s.profile = _Profile(row_size)
    s.build_vols = []
    s.area = None
    s.phase = 0
    s.window_start = t0
    s.broke, s.break_side, s.break_extreme = False, 0, 0.0
    s.break_bars, s.break_vol_sum = 0, 0.0
    s.bars_since_extreme, s.delta_since_extreme = 0, 0.0


def _step_building(s, i, bt, o, h, l, c, v, buy_vol, sell_vol, row_size,
                   window_min, min_area, va_pct, two_rows):
    elapsed = (bt - s.window_start) / (_TPS * 60.0) + 1e-9 >= window_min
    if not elapsed or s.profile.total_volume <= 0:
        s.profile.add_bar_spread(o[i], h[i], l[i], c[i], v[i],
                                  buy_vol[i], sell_vol[i])
        s.build_vols.append(v[i])
        return

    computed = s.profile.value_area(va_pct, two_rows)
    median = float(np.median(s.build_vols)) if s.build_vols else 0.0
    if computed is None or (min_area > 0 and computed["width"] < min_area):
        _restart(s, bt, row_size)     # unusable area, do not arm on it
        return

    s.area = computed
    s.build_median = median
    s.phase = 1
    s.arm_start = bt
    s.broke, s.break_side = False, 0
    s.break_bars, s.break_vol_sum = 0, 0.0
    s.bars_since_extreme, s.delta_since_extreme = 0, 0.0


def _step_armed(s, i, bt, o, h, l, c, v, buy_vol, sell_vol, row_size,
                arm_min, min_break, max_break, allow_long, allow_short,
                decl_max, abs_min_bars, abs_min_delta, ret_min, req_mask,
                stop_buf, max_stop, n, bars, ts, px):
    # A frozen area stops describing anything eventually -- see FIDELITY 6.
    if (bt - s.arm_start) / (_TPS * 60.0) + 1e-9 >= arm_min:
        _restart(s, bt, row_size)
        return None

    val, vah = s.area["val"], s.area["vah"]
    acted, side, edge, far = False, 0, 0.0, 0.0

    if allow_long:
        if l[i] < val - min_break:
            if not s.broke or s.break_side != 1:
                s.broke, s.break_side, s.break_extreme = True, 1, l[i]
                s.bars_since_extreme, s.delta_since_extreme = 0, 0.0
            s.break_bars += 1
            s.break_vol_sum += v[i]
            if l[i] < s.break_extreme:
                s.break_extreme = l[i]
                s.bars_since_extreme, s.delta_since_extreme = 0, 0.0
            else:
                s.bars_since_extreme += 1
                s.delta_since_extreme += (sell_vol[i] - buy_vol[i])
        elif s.broke and s.break_side == 1 and c[i] > val:
            acted, side, edge, far = True, 1, val, vah

    if not acted and allow_short:
        if h[i] > vah + min_break:
            if not s.broke or s.break_side != -1:
                s.broke, s.break_side, s.break_extreme = True, -1, h[i]
                s.bars_since_extreme, s.delta_since_extreme = 0, 0.0
            s.break_bars += 1
            s.break_vol_sum += v[i]
            if h[i] > s.break_extreme:
                s.break_extreme = h[i]
                s.bars_since_extreme, s.delta_since_extreme = 0, 0.0
            else:
                s.bars_since_extreme += 1
                s.delta_since_extreme += (buy_vol[i] - sell_vol[i])
        elif s.broke and s.break_side == -1 and c[i] < vah:
            acted, side, edge, far = True, -1, vah, val

    if not acted:
        return None

    # RaiseCandidate, ported.
    depth = (edge - s.break_extreme) if side > 0 else (s.break_extreme - edge)
    if max_break > 0 and depth > max_break:
        s.broke, s.break_side = False, 0
        return None

    f_decl = (s.build_median > 0 and s.break_bars > 0
              and (s.break_vol_sum / s.break_bars) <= s.build_median * decl_max)
    f_abs = (s.bars_since_extreme >= abs_min_bars
             and s.delta_since_extreme >= abs_min_delta)
    f_ret = s.build_median > 0 and v[i] >= s.build_median * ret_min
    verdict = ((1 if f_decl else 0) | (2 if f_abs else 0)
               | (4 if f_ret else 0))

    entry = c[i]      # EntryOnBarClose -- see FIDELITY 1
    if side > 0:
        stop = s.break_extreme - stop_buf
        if entry - stop > max_stop:
            stop = entry - max_stop
    else:
        stop = s.break_extreme + stop_buf
        if stop - entry > max_stop:
            stop = entry + max_stop
    target = far

    s.broke, s.break_side = False, 0      # ClearBreak(), same as the C#

    if (req_mask & verdict) != req_mask:
        return None        # filtered: no trade, but Armed keeps hunting

    # Fill the entry at the first tick of the NEXT bar (the market order NT8
    # submits the instant the bar closes), same convention as every other
    # bar-close entry ported in this project (e.g. PullbackZone / the
    # PropSim template).
    if i + 1 >= n:
        return None
    entry_tick = int(bars["start"][i + 1])
    if entry_tick >= len(ts):
        return None

    # GAP-THROUGH GUARD -- see FIDELITY 8. The C#'s BarClose mode has no such
    # check: NT8 submits `EnterLong`/`EnterShort` unconditionally and a fill
    # already past its own bracket would just close the trade immediately.
    # The engine's own invariant (`resolve()`: "a trade cannot open already
    # past its own stop or target") would silently drop exactly these rows
    # anyway -- checking it here means Armed keeps hunting on the SAME area
    # instead of the cycle quietly losing a bar to a trade that was never
    # really open, and it is what lets `plugins.py --check`'s stricter,
    # whole-file contract (no such row may ever be RETURNED) pass on real
    # ticks: measured at ~3% of raw reclaims on NQ, all from the same cause,
    # a bar-close signal followed by a gapped next-bar open.
    fill_px = float(px[entry_tick])
    if side > 0:
        gapped = fill_px <= stop or fill_px >= target
    else:
        gapped = fill_px >= stop or fill_px <= target
    if gapped:
        return None

    session_end_ts = int((s.prev_day + 1) * 86400 + _NET_EPOCH_S) * _TPS
    exit_ts = _resolve_exit(ts, px, entry_tick, side, stop, target,
                             session_end_ts)
    s.busy_until = exit_ts
    s.phase = 2
    return dict(entry_tick=entry_tick, dir=np.int8(side), stop=stop,
                target=target)


class AreaTrap(Strategy):
    """Failed break of a rolling session volume-profile value area. Spec:
    `ninjascript/AreaTrapCore.cs` (`AtEngine`); `docs/source-strategy.md` for
    the trader's original statement. 439 sessions on the raw geometry alone
    measured PF 0.86-0.97 (`docs/feasibility.md`) -- this port exists to let
    the three volume filters be measured on the tick tape, not to claim edge.
    """
    name, label = "area_trap", "AreaTrap (failed break of the session value area)"
    uses_ticks = True
    full_session = FULL_SESSION

    params = {
        "window_minutes": Param(10, 2, 60, "profile build window, minutes",
                                fixed=True),
        "arm_minutes": Param(10, 2, 120, "a frozen area may hunt this long "
                                        "before it is stale, minutes",
                             fixed=True),
        "contracts": Param(1, 1, 100, "position size, contracts", fixed=True),
        "value_area_percent": Param(0.70, 0.5, 0.95, "value-area coverage "
                                                      "target", fixed=True),
        "ticks_per_level": Param(1, 1, 20, "profile row size, ticks",
                                 fixed=True),
        "expand_two_rows": Param(0, 0, 1, "CBOT pair-row value-area rule "
                                          "(0 = single-row, what feasibility.md "
                                          "measured)", fixed=True),
        "entry_on_bar_close": Param(1, 0, 1, "bar-close entry -- the only mode "
                                             "ported, see FIDELITY 1",
                                    fixed=True),
        "min_break_points": Param(0.50, 0.0, 5.0, "how far past VAL/VAH counts "
                                                  "as a break, points",
                                  fixed=True),
        "max_break_points": Param(0.0, 0.0, 50.0, "break deeper than this is "
                                                   "too deep to fade, points; "
                                                   "0 = off", fixed=True),
        "allow_long": Param(1, 0, 1, "trade long side", fixed=True),
        "allow_short": Param(1, 0, 1, "trade short side", fixed=True),
        "use_declining_volume": Param(0, 0, 1, "require declining break-bar "
                                               "volume (OFF by default -- the "
                                               "experiment)", fixed=True),
        "declining_volume_max": Param(0.80, 0.1, 1.0, "mean break-bar volume / "
                                                      "median build-bar volume "
                                                      "ceiling", fixed=True),
        "use_absorption": Param(0, 0, 1, "require absorption at the extreme "
                                         "(OFF by default)", fixed=True),
        "absorption_min_bars": Param(2, 1, 20, "bars the extreme has to "
                                               "survive", fixed=True),
        "absorption_min_delta": Param(200, 0, 5000, "net against-the-break "
                                                    "aggressor volume since "
                                                    "the extreme", fixed=True),
        "use_returning_volume": Param(0, 0, 1, "require reclaim-bar volume "
                                               "above the build median (OFF "
                                               "by default)", fixed=True),
        "returning_volume_min": Param(1.00, 0.1, 5.0, "reclaim-bar volume / "
                                                      "median build-bar volume "
                                                      "floor", fixed=True),
        "stop_buffer_points": Param(1.00, 0.0, 10.0, "stop beyond the break "
                                                     "extreme, points",
                                    fixed=True),
        "max_stop_points": Param(12.0, 1.0, 50.0, "stop cap from entry, "
                                                  "points", fixed=True),
        "min_area_points": Param(0.0, 0.0, 20.0, "reject a value area "
                                                 "narrower than this, points; "
                                                 "0 = off", fixed=True),
    }

    def risk_ticks(self, p) -> float:
        return float(p["max_stop_points"]) / self.tick

    def entries(self, bars, tape, p):
        # `bars` is deliberately unused -- see `_build_bars`'s note: AreaTrap
        # needs 30s bars specifically, not whatever `tf_secs` the caller ran
        # the engine at (`plugins.py`'s own smoke test always calls at 300s).
        sigs = signals(_build_bars(tape, TF_SECS), tape, p)
        if not sigs:
            return _EMPTY4
        et = np.array([s["entry_tick"] for s in sigs], np.int64)
        dr = np.array([s["dir"] for s in sigs], np.int8)
        st = np.array([s["stop"] for s in sigs])
        tg = np.array([s["target"] for s in sigs])
        return et, dr, st, tg


# ---------------------------------------------------------------- selfcheck
def _fx_tape(bars30, sod0=9 * 3600 + 30 * 60, day0=20000):
    """Four prints per 30s bar -- open, both extremes in path order, close.
    Side: +1 on an up print, -1 on a down print (aggressor proxy), matching
    the fixture convention `pullback_zone.py` already uses for this shape.
    `bars30` is a list of (o, h, l, c) 4-tuples -- the buy/sell split is
    irrelevant to every selfcheck below (all three volume filters are OFF at
    the ported defaults, so `entries()` never reads it), so the fixture does
    not try to steer it."""
    ts, px, side = [], [], []
    base = (int(day0) * 86400 + _NET_EPOCH_S + int(sod0)) * _TPS
    prev = None
    for k, (o, h, l, c) in enumerate(bars30):
        t0 = base + k * TF_SECS * _TPS
        mid = (h, l) if c < o else (l, h)
        for dt, v in zip((0, 7, 14, 21), (o, mid[0], mid[1], c)):
            ts.append(t0 + dt * _TPS)
            px.append(v)
            side.append(1 if prev is None or v >= prev else -1)
            prev = v
    n = len(ts)
    vol = np.ones(n, np.int64)
    return dict(ts=np.array(ts, np.int64), px=np.array(px, np.float64),
                vol=vol, side=np.array(side, np.int8))


def _flat(n, px, w=0.5):
    return [(px, px + w, px - w, px) for _ in range(n)]


def _ramp(n, a, z, w=0.25):
    out = []
    for k in range(n):
        o = a + (z - a) * k / n
        c = a + (z - a) * (k + 1) / n
        out.append((o, max(o, c) + w, min(o, c) - w, c))
    return out


def _build_section(counts, row_size=0.25, first_row_px=99.5):
    """A center-weighted volume profile: `counts[k]` single-row bars sit at
    `first_row_px + k*row_size`, so the histogram this actually builds is
    exactly `counts`, unambiguously (a bar with h == l cannot straddle a row
    boundary). Deliberately NOT a run of identical bars or a smooth path
    through price -- either spreads volume evenly across every row it
    touches (`AtProfile.AddBarSpread`'s honest-approximation design) and,
    once the resulting histogram is flat or symmetric-by-construction, the
    POC tie (`np.argmax` picks the first occurrence) resolves to an edge
    row and the value-area expansion only ever grows one way -- a real
    property of that shape, not a bug, but not what a "clean value area"
    fixture is meant to exercise."""
    out = []
    for k, n in enumerate(counts):
        px = first_row_px + k * row_size + row_size / 2.0
        out += [(px, px, px, px)] * n
    return out


def _positive_case():
    """A session that builds a clean, center-weighted value area (rows
    99.5/99.75/100.0/100.25/100.5, counts 2/4/10/4/2 -> POC 100.0, VAL~99.75,
    VAH~100.5), breaks below VAL, and closes back inside on the reclaim bar
    -- a trade under the ALL-FILTERS-OFF defaults (the only ones this port
    needs to exercise: all three volume filters are ported but OFF by
    default, same as the .cs)."""
    bars = []
    bars += _build_section([2, 4, 10, 4, 2])           # 22 bars, 30 needed by
    bars += _build_section([2, 4, 10, 4, 2])[:8]       # window_minutes=15 below
    bars += _ramp(4, 100.0, 98.5, w=0.10)              # break below VAL
    bars += _ramp(2, 98.5, 100.05, w=0.10)             # reclaim: closes back inside
    bars += _flat(10, 100.05, w=0.3)                   # drift, no second break
    return bars


def _negative_case():
    """Same shape, but the break never actually leaves the area (min_break_points
    is not cleared) -- must produce nothing."""
    bars = []
    bars += _build_section([2, 4, 10, 4, 2])
    bars += _build_section([2, 4, 10, 4, 2])[:8]
    bars += _ramp(6, 100.0, 99.7, w=0.05)              # inside VAL - 0.5, no break
    bars += _flat(10, 99.8, w=0.2)
    return bars


def _selfcheck_value_area():
    px = np.array([98.0, 98.5, 99.0, 99.5, 100.0, 100.5, 101.0])
    vol = np.array([5.0, 10.0, 20.0, 50.0, 20.0, 10.0, 5.0])
    va = _value_area(px, vol, 0.70, two_rows=False)
    assert va is not None
    assert va["poc"] == 99.5
    assert va["val"] <= 99.5 <= va["vah"]
    total = vol.sum()
    inside = vol[(px >= va["val"]) & (px <= va["vah"])].sum()
    assert inside / total >= 0.70 - 1e-9
    print("value area OK")


def _selfcheck_profile_row_and_spread():
    prof = _Profile(row_size=0.25)
    assert prof.row_of(100.0) == 400
    assert prof.row_of(99.99) == 399
    # h=100.25, l=99.5 -> 4 rows (99.5/99.75/100.0/100.25) so 8 divides evenly
    # and the truncation quirk below (see AtProfile.AddBarSpread in the C#:
    # buy/sell shares are NOT given the total's remainder redistribution) does
    # not muddy this particular assertion.
    prof.add_bar_spread(o=100.0, h=100.25, l=99.5, c=100.0, v=8, buy=8, sell=0)
    total = sum(prof.tot.values())
    assert total == 8, prof.tot
    assert sum(prof.buy.values()) == 8 and sum(prof.sell.values()) == 0
    print("profile row/spread OK")


def _selfcheck_positive():
    bars_fx = _positive_case()
    t = _fx_tape(bars_fx)
    bars = _build_bars(t, TF_SECS)
    p = dict(PARAMS_DEFAULT, window_minutes=1)   # 40 bars @ 30s = 20 min; shrink
    # window_minutes=1 -> the profile freezes after ~2 bars of the flat
    # section elapse; use a value that lands the freeze inside the 40-bar
    # flat build so VAL/VAH are computed from real spread, not one bar.
    p["window_minutes"] = 15   # 30 bars of 30s, well inside the 40-bar flat build
    sigs = signals(bars, t, p)
    assert sigs, "expected at least one trade on the positive fixture"
    s = sigs[0]
    assert s["dir"] == 1, sigs                    # long: broke below VAL
    assert s["stop"] < s["target"], s
    fill = t["px"][s["entry_tick"]]
    assert s["stop"] < fill < s["target"], (s, fill)
    print("positive case OK:", len(sigs), "trade(s)")


def _selfcheck_negative():
    bars_fx = _negative_case()
    t = _fx_tape(bars_fx)
    bars = _build_bars(t, TF_SECS)
    p = dict(PARAMS_DEFAULT, window_minutes=15)
    sigs = signals(bars, t, p)
    assert sigs == [], sigs
    print("negative case (no break) OK")


def _selfcheck_truncation_invariant():
    """No-lookahead: every field of a raised candidate is frozen at the
    reclaim bar's own close. Truncating the tape one tick after the fill
    must not change the stop/target already decided (same invariant as
    PullbackZone's episodes selfcheck)."""
    bars_fx = _positive_case()
    t = _fx_tape(bars_fx)
    bars = _build_bars(t, TF_SECS)
    p = dict(PARAMS_DEFAULT, window_minutes=15)
    sigs = signals(bars, t, p)
    assert sigs
    s = sigs[0]
    cut = s["entry_tick"] + 2
    t2 = {k: (v[:cut] if hasattr(v, "__len__") else v) for k, v in t.items()}
    bars2 = _build_bars(t2, TF_SECS)
    sigs2 = signals(bars2, t2, p)
    assert sigs2, "truncated tape must still reproduce the same first trade"
    s2 = sigs2[0]
    assert s2["entry_tick"] == s["entry_tick"]
    assert abs(s2["stop"] - s["stop"]) < 1e-9
    assert abs(s2["target"] - s["target"]) < 1e-9
    print("truncation invariant OK")


def _selfcheck_strategy_contract():
    strat = AreaTrap()
    for k in strat.params:
        assert k in PARAMS_DEFAULT, k
    for k, v in PARAMS_DEFAULT.items():
        assert k in strat.params, k
        assert strat.params[k].lo <= v <= strat.params[k].hi, k
        assert strat.params[k].default == v, k
    assert strat.risk_ticks(PARAMS_DEFAULT) == PARAMS_DEFAULT["max_stop_points"] / TICK

    bars_fx = _positive_case()
    t = _fx_tape(bars_fx)
    bars = _build_bars(t, TF_SECS)
    p = dict(PARAMS_DEFAULT, window_minutes=15)
    et, dr, st, tg = strat.entries(bars, t, p)
    assert et.dtype == np.int64 and dr.dtype == np.int8
    assert len(et) == len(dr) == len(st) == len(tg) >= 1
    print("strategy contract OK")


if __name__ == "__main__":
    _selfcheck_value_area()
    _selfcheck_profile_row_and_spread()
    _selfcheck_positive()
    _selfcheck_negative()
    _selfcheck_truncation_invariant()
    _selfcheck_strategy_contract()
