#!/usr/bin/env python3
"""
VAL break -> reclaim measurement, implementation A.

Cycle, per session, per NON-OVERLAPPING W-minute window from the RTH open:
  BUILD  volume-at-price (sparse, 0.25 tick rows, each 1s bar's volume spread
         uniformly low..high inclusive)
  VALUE AREA at window close: POC, expand 1-vs-1 until >= 70% -> VAL/VAH, FROZEN
  ARM    for W minutes: break below VAL, then reclaim (E_close / E_tick)
  TRADE  CLOSE / STOPMKT / LIMIT, long only, stop = break_low - 1.00 (- 2t slip),
         target = VAH.

Run: python3 val_reclaim_measure_A.py            (full run, both W)
     python3 val_reclaim_measure_A.py --selftest (unit checks only)
"""
import sys, json
import numpy as np

NPZ  = "/home/javlo/Code Projects/main-project/projects/Trading/NQData/NQ_continuous_1s.npz"
TICK = 0.25
PPT  = 20.0          # $ per point
COMM = 5.76          # $ round turn commission (the other $10 of the $15.76 is the
                     # 2 ticks of exit slippage, kept explicit in the prices)
SLIP_T = 2           # ticks of slippage on stop-market entry and on stop exit
VA_PCT = 0.70
RTH_OPEN, RTH_CLOSE = 9*3600+30*60, 16*3600
MIN_BUILD_BARS = 60  # a build window with fewer traded seconds than this is skipped


# ---------------------------------------------------------------- value area
def value_area(low, high, vol, pct=VA_PCT):
    """Sparse volume-at-price -> (poc, val, vah, total). Prices are floats on the
    0.25 grid. Returns None if the window carries no volume."""
    total = float(vol.sum())
    if total <= 0:
        return None
    lo_i = np.rint(low  / TICK).astype(np.int64)
    hi_i = np.rint(high / TICK).astype(np.int64)
    base = lo_i.min()
    n    = int(hi_i.max() - base) + 1
    w    = vol.astype(np.float64) / (hi_i - lo_i + 1)   # uniform per tick spanned
    # difference-array spread: O(bars), exact sum
    d = np.zeros(n + 1)
    np.add.at(d, lo_i - base, w)
    np.add.at(d, hi_i - base + 1, -w)
    hist = np.cumsum(d)[:n]
    keep = hist > 1e-9                    # SPARSE: untraded prices are absent
    idx  = np.flatnonzero(keep)
    h    = hist[idx]
    p    = int(np.argmax(h))              # tie -> lowest price among maxima
    lo_p = hi_p = p
    acc  = h[p]
    need = total * pct
    last = len(h) - 1
    while acc < need and (lo_p > 0 or hi_p < last):
        up   = h[hi_p + 1] if hi_p < last else -1.0
        dn   = h[lo_p - 1] if lo_p > 0    else -1.0
        if up >= dn:                      # tie -> take the upper row
            hi_p += 1; acc += up
        else:
            lo_p -= 1; acc += dn
    px = (idx + base) * TICK
    return float(px[p]), float(px[lo_p]), float(px[hi_p]), total


# ---------------------------------------------------------------- trade sim
def resolve(hi, lo, cl, ts, start, end, entry_px, stop_px, target_px):
    """First touch wins over bars [start, end). Stop checked before target inside
    the same bar (pessimistic tie-break). Returns (exit_px, kind, exit_i)."""
    for i in range(start, end):
        if lo[i] <= stop_px:
            return stop_px - SLIP_T * TICK, "stop", i
        if hi[i] >= target_px:
            return target_px, "target", i
    if end > start:
        return float(cl[end - 1]), "unresolved", end - 1
    return entry_px, "unresolved", start - 1


def pnl_usd(entry, exit_):
    return (exit_ - entry) * PPT - COMM


# ---------------------------------------------------------------- one session
def run_session(ts, hi, lo, cl, vo, sod, W):
    """Yield one dict per armed window that produced a break + at least E_tick."""
    wsec = W * 60
    sess_end_ts = ts[-1] + 1
    out = []
    t_open = ts[0] - (sod[0] - RTH_OPEN)      # epoch ts of 09:30:00 for this day
    k = 0
    while True:
        t0 = t_open + k * wsec
        t1 = t0 + wsec                         # build ends / arm starts
        t2 = t1 + wsec                         # arm timeout
        k += 1
        if t0 >= sess_end_ts:
            break
        b0, b1 = np.searchsorted(ts, [t0, t1])
        if b1 - b0 < MIN_BUILD_BARS:
            continue
        va = value_area(lo[b0:b1], hi[b0:b1], vo[b0:b1])
        if va is None:
            continue
        poc, VAL, VAH, wvol = va
        if VAH <= VAL:
            continue
        # ---- arm window (strictly after the build window)
        a0, a1 = np.searchsorted(ts, [t1, min(t2, sess_end_ts)])
        assert b1 <= a0, "build/arm overlap"
        if a1 - a0 < 2:
            continue
        # break
        below = np.flatnonzero(lo[a0:a1] < VAL)
        if below.size == 0:
            continue
        brk = a0 + below[0]
        # running min of low over bars that are below VAL, cumulative
        blow_idx = a0 + below
        blow_run = np.minimum.accumulate(lo[blow_idx])

        def break_low_before(i):
            """min low over below-VAL bars with ts strictly < ts[i]. No lookahead."""
            j = np.searchsorted(blow_idx, i) - 1
            return None if j < 0 else float(blow_run[j])

        # E_tick: first 1s bar strictly after the break bar with high >= VAL + tick
        need_hi = VAL + TICK
        cand = np.flatnonzero(hi[brk + 1:a1] >= need_hi - 1e-9)
        e_tick = (brk + 1 + cand[0]) if cand.size else None

        # E_close: first 30s bar (\:00/\:30 grid) fully inside the arm window whose
        # close is above VAL and whose slot_end is after the break bar
        e_close = None          # index of the last 1s bar in the slot
        e_close_ts = None       # slot_end (the decision moment)
        slots = ts[a0:a1] // 30
        bounds = np.flatnonzero(np.diff(slots)) + 1
        starts = np.concatenate(([0], bounds))
        ends = np.concatenate((bounds, [a1 - a0]))
        for s, e in zip(starts, ends):
            slot_end = (int(slots[s]) + 1) * 30
            if slot_end > min(t2, sess_end_ts):
                break
            last = a0 + e - 1
            if slot_end <= ts[brk]:
                continue
            if cl[last] > VAL + 1e-9:
                e_close, e_close_ts = last, slot_end
                break

        if e_tick is None and e_close is None:
            continue   # no reclaim of any kind

        rec = dict(VAL=VAL, VAH=VAH, POC=poc, t1=t1, arm_end=min(t2, sess_end_ts),
                   a1=a1, brk=brk, e_tick=(None if e_tick is None else int(e_tick)),
                   e_close=(None if e_close is None else int(e_close)),
                   e_close_ts=e_close_ts)

        # ---------------- STOPMKT on E_tick
        bl = break_low_before(e_tick) if e_tick is not None else None
        if bl is not None:
            stop = bl - 1.00
            entry = VAL + TICK + SLIP_T * TICK
            if stop < entry:
                ex, kind, _ = resolve(hi, lo, cl, ts, e_tick + 1, a1, entry, stop, VAH)
                rec["stopmkt"] = dict(entry=entry, exit=ex, kind=kind,
                                      pnl=pnl_usd(entry, ex), pts=ex - entry)

        # ---------------- CLOSE / CLOSE+1t / LIMIT on E_close
        if e_close is not None:
            bl = break_low_before(e_close + 1)   # bars with ts <= ts[e_close] < slot_end
            if bl is not None:
                stop = bl - 1.00
                start = e_close + 1
                for name, entry in (("close", float(cl[e_close])),
                                    ("close_1t", float(cl[e_close]) + TICK)):
                    if stop < entry:
                        ex, kind, xi = resolve(hi, lo, cl, ts, start, a1, entry, stop, VAH)
                        rec[name] = dict(entry=entry, exit=ex, kind=kind,
                                         pnl=pnl_usd(entry, ex), pts=ex - entry,
                                         exit_i=xi)
                # retest: does price touch VAL again before the CLOSE trade resolves?
                if "close" in rec:
                    xi = rec["close"]["exit_i"]
                    seg = lo[start:max(xi + 1, start)]
                    rec["retest"] = bool(seg.size and (seg <= VAL + 1e-9).any())
                # LIMIT, both fill conventions
                for tag, lvl in (("limit_touch", VAL), ("limit_through", VAL - TICK)):
                    fill = None
                    for i in range(start, a1):
                        if lo[i] <= lvl + 1e-9:
                            fill = i; break
                        if hi[i] >= VAH:
                            break            # target reached without us -> MISS
                    if fill is None:
                        rec[tag] = dict(miss=True)
                    elif stop < VAL:
                        ex, kind, _ = resolve(hi, lo, cl, ts, fill + 1, a1, VAL, stop, VAH)
                        rec[tag] = dict(miss=False, entry=VAL, exit=ex, kind=kind,
                                        pnl=pnl_usd(VAL, ex), pts=ex - VAL)
        out.append(rec)
    return out


# ---------------------------------------------------------------- reporting
def stats(pnls):
    a = np.asarray(pnls, dtype=float)
    n = a.size
    if n == 0:
        return dict(n=0, winRate=0.0, avgUsd=0.0, avgPts=0.0, profitFactor=0.0,
                    worstUsd=0.0, medLoss=0.0, p90Loss=0.0)
    wins, losses = a[a > 0], a[a <= 0]
    gp, gl = wins.sum(), -losses.sum()
    L = -losses if losses.size else np.array([0.0])
    return dict(n=int(n), winRate=float((a > 0).mean()),
                avgUsd=float(a.mean()), avgPts=float(a.mean() / PPT),
                profitFactor=float(gp / gl) if gl > 0 else float("inf"),
                worstUsd=float(a.min()),
                medLoss=float(np.median(L)), p90Loss=float(np.percentile(L, 90)))


def main():
    d = np.load(NPZ, allow_pickle=True)
    ts = d["ts"]
    px_adj = d["adj"].astype(np.float64)
    hi = d["high"].astype(np.float64) + px_adj
    lo = d["low"].astype(np.float64) + px_adj
    cl = d["close"].astype(np.float64) + px_adj
    vo = d["volume"].astype(np.float64)

    # UTC epoch -> US/Eastern second-of-day and calendar date
    import pandas as pd
    et = pd.DatetimeIndex(pd.to_datetime(ts, unit="s", utc=True)).tz_convert("America/New_York")
    sod = (et.hour * 3600 + et.minute * 60 + et.second).to_numpy()
    day = (et.year * 10000 + et.month * 100 + et.day).to_numpy()
    m = (sod >= RTH_OPEN) & (sod < RTH_CLOSE)
    ts, hi, lo, cl, vo, sod, day = (x[m] for x in (ts, hi, lo, cl, vo, sod, day))
    assert (np.diff(ts) > 0).all(), "ts not strictly increasing"
    cuts = np.flatnonzero(np.diff(day)) + 1
    sess = np.split(np.arange(ts.size), cuts)
    print(f"RTH bars {ts.size:,}  sessions {len(sess)}  "
          f"{day[0]} -> {day[-1]}", file=sys.stderr)

    res = {}
    for W in (10, 30):
        recs = []
        for s in sess:
            recs += run_session(ts[s], hi[s], lo[s], cl[s], vo[s], sod[s], W)
        res[W] = recs
        print(f"W={W}: {len(recs)} armed windows with a break+reclaim", file=sys.stderr)
    return res, dict(sessions=len(sess), bars=int(ts.size),
                     first=int(day[0]), last=int(day[-1]))


# ---------------------------------------------------------------- self-check
def selftest():
    # 1) value area on a hand-built histogram.
    # bars: (low, high, vol) -> volume per tick
    lo = np.array([100.00, 100.25, 100.50, 100.75])
    hi = np.array([100.00, 100.25, 100.50, 100.75])
    vo = np.array([10.0, 100.0, 40.0, 5.0])          # total 155, 70% = 108.5
    poc, val, vah, tot = value_area(lo, hi, vo)
    assert (poc, tot) == (100.25, 155.0), (poc, tot)
    # POC 100 -> up(40) vs dn(10): take 40 -> 140 >= 108.5. VA = [100.25, 100.50]
    assert (val, vah) == (100.25, 100.50), (val, vah)

    # 2) uniform spread across a spanned bar
    poc, val, vah, tot = value_area(np.array([100.0]), np.array([100.75]),
                                    np.array([8.0]))
    # flat 4-tick bar: 2 per tick, 70% of 8 = 5.6 -> POC(tie)=lowest, expand up twice
    assert (tot, val, vah) == (8.0, 100.0, 100.50), (val, vah, tot)

    # 3) sparse: an untraded tick between two islands must be absent, so the
    #    1-vs-1 expansion steps over it rather than stalling on a zero row.
    poc, val, vah, tot = value_area(np.array([100.0, 101.0]), np.array([100.0, 101.0]),
                                    np.array([10.0, 9.0]))
    assert (poc, val, vah) == (100.0, 100.0, 101.0), (poc, val, vah)

    # 4) resolution: stop wins the tie inside one bar
    h = np.array([10.0, 12.0]); l = np.array([10.0, 8.0]); c = np.array([10.0, 9.0])
    ex, kind, _ = resolve(h, l, c, None, 0, 2, 10.0, 9.0, 11.0)
    assert kind == "stop" and ex == 9.0 - 0.5, (kind, ex)

    # 5) P&L accounting: +1 point long = $20 - 5.76
    assert abs(pnl_usd(100.0, 101.0) - (20.0 - 5.76)) < 1e-9
    print("selftest OK", file=sys.stderr)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest(); sys.exit(0)
    selftest()
    res, meta = main()

    report = dict(meta=meta, W={})
    for W, recs in res.items():
        n_tick = sum(1 for r in recs if r["e_tick"] is not None)
        n_close = sum(1 for r in recs if r["e_close"] is not None)
        overlap = sum(1 for r in recs if r["e_tick"] is not None and r["e_close"] is not None)
        only = [r for r in recs if r["e_close"] is None and r["e_tick"] is not None]
        close_only = sum(1 for r in recs if r["e_close"] is not None and r["e_tick"] is None)
        rows = {}
        rows["CLOSE (no slip)"] = stats([r["close"]["pnl"] for r in recs if "close" in r])
        rows["CLOSE +1 tick"] = stats([r["close_1t"]["pnl"] for r in recs if "close_1t" in r])
        rows["STOPMKT (all E_tick)"] = stats([r["stopmkt"]["pnl"] for r in recs if "stopmkt" in r])
        rows["STOPMKT (E_close subset)"] = stats([r["stopmkt"]["pnl"] for r in recs
                                                  if "stopmkt" in r and r["e_close"] is not None])
        rows["STOPMKT (E_tick-only)"] = stats([r["stopmkt"]["pnl"] for r in only if "stopmkt" in r])
        for tag, label in (("limit_touch", "LIMIT @VAL touch-fill"),
                           ("limit_through", "LIMIT @VAL through-fill")):
            att = [r[tag] for r in recs if tag in r]
            fills = [x["pnl"] for x in att if not x["miss"]]
            s = stats(fills)
            s["attempts"] = len(att); s["misses"] = sum(1 for x in att if x["miss"])
            rows[label] = s
        retests = [r["retest"] for r in recs if "retest" in r]
        unres = {k: sum(1 for r in recs if k in r and r[k].get("kind") == "unresolved")
                 for k in ("close", "stopmkt")}
        report["W"][W] = dict(n_windows_with_break_and_reclaim=len(recs),
                              n_e_tick=n_tick, n_e_close=n_close, overlap=overlap,
                              n_tick_only=len(only), n_close_only=close_only,
                              retest_n=len(retests), retest_frac=(float(np.mean(retests))
                                                                  if retests else None),
                              unresolved=unres, rows=rows)
    print(json.dumps(report, indent=1, default=float))
