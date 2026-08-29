#!/usr/bin/env python3
"""Value-area VAL-reclaim measurement -- implementation B.

Build a volume-at-price value area over each non-overlapping W-minute window from
the RTH open, freeze it, arm for W minutes, and take the long side of a VAL break
+ reclaim three different ways (CLOSE / STOPMKT / LIMIT-at-VAL).

    python3 va_reclaim_B.py             full report (W=10 and W=30)
    python3 va_reclaim_B.py --selfcheck asserts on the VA + resolution logic
"""
import json, sys
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

NPZ  = "/home/javlo/Code Projects/main-project/projects/Trading/NQData/NQ_continuous_1s.npz"
TICK = 0.25
PT   = 20.0      # $ per NQ point
COMM = 5.76      # $ round-turn commission; slippage is explicit in the prices
VA_FRAC   = 0.70
STOP_BUF  = 1.00 # points below break_low
SLIP_EXIT = 2 * TICK   # stop-exit slippage
SLIP_ENTR = 2 * TICK   # stop-market entry slippage
OPEN_SOD  = 9 * 3600 + 1800
CLOSE_SOD = 16 * 3600


# ------------------------------------------------------------------ data
def load_rth():
    d = np.load(NPZ, allow_pickle=True)
    ts = d["ts"].astype("int64")
    idx = pd.DatetimeIndex(pd.to_datetime(ts, unit="s", utc=True)).tz_convert(ZoneInfo("America/New_York"))
    sod = np.asarray(idx.hour * 3600 + idx.minute * 60 + idx.second)
    m = (sod >= OPEN_SOD) & (sod < CLOSE_SOD)
    # Raw front-month prices. Every roll in this file lands at 00:00 ET (checked in
    # meta['rolls']), so no RTH session is cut by a contract change -> no adj needed.
    return dict(ts=ts[m], sod=sod[m], day=np.asarray(idx.strftime("%Y-%m-%d"))[m],
                high=d["high"][m].astype(np.float64), low=d["low"][m].astype(np.float64),
                close=d["close"][m].astype(np.float64), volume=d["volume"][m].astype(np.float64))


# ------------------------------------------------------------ value area
def value_area(low, high, vol):
    """Sparse volume-at-price on the 0.25 grid; POC then 1-vs-1 expansion to 70%.
    Reads ONLY the arrays passed in -- i.e. only the build window."""
    lo_i = np.rint(low / TICK).astype(np.int64)
    hi_i = np.rint(high / TICK).astype(np.int64)
    per = vol / (hi_i - lo_i + 1)          # bar volume spread uniformly low..high incl.
    base, top = lo_i.min(), hi_i.max()
    diff = np.zeros(top - base + 2)        # difference array -> O(bars), not O(bars*ticks)
    np.add.at(diff, lo_i - base, per)
    np.add.at(diff, hi_i - base + 1, -per)
    hist = np.cumsum(diff)[:-1]
    keep = np.nonzero(hist > 1e-9)[0]      # SPARSE: untraded prices are absent
    h = hist[keep]
    need = VA_FRAC * h.sum()
    poc = int(h.argmax())
    lo_k = hi_k = poc
    acc = h[poc]
    while acc < need and (lo_k > 0 or hi_k < len(h) - 1):
        up = h[hi_k + 1] if hi_k < len(h) - 1 else -1.0
        dn = h[lo_k - 1] if lo_k > 0 else -1.0
        if up >= dn:                       # tie -> upper row (declared deviation)
            hi_k += 1; acc += up
        else:
            lo_k -= 1; acc += dn
    px = lambda k: float((keep[k] + base) * TICK)
    return px(lo_k), px(hi_k), px(poc)


# ------------------------------------------------------------ resolution
def resolve(L, H, C, start, stop_px, tgt_px):
    """First touch wins scanning bars [start:]. Same-bar stop+target -> stop
    (1s bars give no intrabar order; the conservative read)."""
    if start >= len(L):
        return C[-1], len(L) - 1, "unresolved"
    s = np.nonzero(L[start:] <= stop_px)[0]
    t = np.nonzero(H[start:] >= tgt_px)[0]
    si = start + int(s[0]) if len(s) else None
    ti = start + int(t[0]) if len(t) else None
    if si is not None and (ti is None or si <= ti):
        return stop_px - SLIP_EXIT, si, "stop"
    if ti is not None:
        return tgt_px, ti, "target"
    return C[-1], len(L) - 1, "unresolved"


def usd(entry, exit_px):
    p = exit_px - entry
    return p, p * PT - COMM


# ------------------------------------------------------------ the cycle
def scan(W, D):
    ts, sod, day = D["ts"], D["sod"], D["day"]
    hi, lo, cl, vol = D["high"], D["low"], D["close"], D["volume"]
    bnd = np.nonzero(np.r_[True, day[1:] != day[:-1], True])[0]
    Wsec = W * 60
    events, n_win = [], 0
    for b in range(len(bnd) - 1):
        a, z = bnd[b], bnd[b + 1]
        ssod, sts = sod[a:z], ts[a:z]
        sh, sl, sc, sv = hi[a:z], lo[a:z], cl[a:z], vol[a:z]
        for k in range((CLOSE_SOD - OPEN_SOD) // Wsec):
            t0 = OPEN_SOD + k * Wsec
            bs, be = np.searchsorted(ssod, [t0, t0 + Wsec])
            if be - bs < 10:                       # dead window, no VA to speak of
                continue
            n_win += 1
            val, vah, poc = value_area(sl[bs:be], sh[bs:be], sv[bs:be])
            # ---- VA FROZEN. Nothing below touches an index < be. ----
            ae = np.searchsorted(ssod, t0 + 2 * Wsec)
            L, H, C, T = sl[be:ae], sh[be:ae], sc[be:ae], sts[be:ae]
            if len(L) < 5:
                continue
            blw = np.nonzero(L < val)[0]           # BREAK: trades strictly below VAL
            if not len(blw):
                continue
            fb = int(blw[0])
            cummin = np.minimum.accumulate(L[fb:]) # break_low[i] = cummin[i-fb]

            et = np.nonzero(H[fb:] >= val + TICK)[0]
            i_tick = fb + int(et[0]) if len(et) else None

            i_close = None                         # first 30s bar closing above VAL
            g = T // 30
            gs_all = np.nonzero(np.r_[True, g[1:] != g[:-1]])[0]
            ge_all = np.r_[gs_all[1:] - 1, len(g) - 1]
            for gs, ge in zip(gs_all, ge_all):
                if ge < fb:                        # break must exist by the bar's close
                    continue
                if C[ge] > val:
                    i_close = int(ge)
                    break

            events.append(dict(day=day[a], t0=t0, val=val, vah=vah,
                               fb=fb, i_tick=i_tick, i_close=i_close,
                               L=L, H=H, C=C))
    return events, n_win, len(bnd) - 1


def brklow(e, i):
    """break_low using ONLY bars up to and including index i. No lookahead."""
    return float(np.min(e["L"][e["fb"]:i + 1]))


# ------------------------------------------------------------ variants
def run_variants(W, events):
    out = {k: {"pts": [], "usd": []} for k in
           ("close", "close1t", "stopmkt_all", "stopmkt_sub", "limit_touch",
            "limit_through", "tickonly")}
    unres = {k: 0 for k in out}
    misses = {"limit_touch": 0, "limit_through": 0}
    retest_hit = retest_n = 0

    for e in events:
        val, vah, L, H, C = e["val"], e["vah"], e["L"], e["H"], e["C"]
        ic, it = e["i_close"], e["i_tick"]

        if it is not None:
            stop = brklow(e, it) - STOP_BUF
            entry = val + TICK + SLIP_ENTR
            # stop-market fires intrabar -> resolution may start on the same bar
            xp, xi, st = resolve(L, H, C, it, stop, vah)
            p, u = usd(entry, xp)
            key = "stopmkt_all"
            out[key]["pts"].append(p); out[key]["usd"].append(u)
            if st == "unresolved": unres[key] += 1
            if ic is None:                                   # E_tick-only group
                out["tickonly"]["pts"].append(p); out["tickonly"]["usd"].append(u)
                if st == "unresolved": unres["tickonly"] += 1
            else:
                out["stopmkt_sub"]["pts"].append(p); out["stopmkt_sub"]["usd"].append(u)
                if st == "unresolved": unres["stopmkt_sub"] += 1

        if ic is None:
            continue
        stop = brklow(e, ic) - STOP_BUF
        # entry at the close of the E_close bar -> intrabar is done, resolve from ic+1
        xp, xi, st = resolve(L, H, C, ic + 1, stop, vah)
        for key, ent in (("close", C[ic]), ("close1t", C[ic] + TICK)):
            p, u = usd(ent, xp)
            out[key]["pts"].append(p); out[key]["usd"].append(u)
            if st == "unresolved": unres[key] += 1

        # RETEST: does price touch VAL again before the CLOSE trade resolves?
        retest_n += 1
        seg = L[ic + 1:xi + 1]
        if len(seg) and seg.min() <= val:
            retest_hit += 1

        # LIMIT at VAL, resting from the E_close moment, alive until the trade resolves
        for key, lim in (("limit_touch", val), ("limit_through", val - TICK)):
            hits = np.nonzero(L[ic + 1:xi + 1] <= lim)[0]
            if not len(hits):
                misses[key] += 1
                continue
            f = ic + 1 + int(hits[0])
            fxp, _, fst = resolve(L, H, C, f, stop, vah)   # fill is intrabar -> incl. bar f
            p, u = usd(val, fxp)                            # filled at VAL, no slippage
            out[key]["pts"].append(p); out[key]["usd"].append(u)
            if fst == "unresolved": unres[key] += 1

    return out, unres, misses, retest_hit, retest_n


def stats(window, entry, d, note=""):
    u = np.asarray(d["usd"]); p = np.asarray(d["pts"])
    if len(u) == 0:
        return dict(window=window, entry=entry, n=0, winRate=0.0, avgUsd=0.0,
                    profitFactor=0.0, worstUsd=0.0, note=(note + " no trades").strip())
    w, l = u[u > 0], u[u <= 0]
    pf = float(w.sum() / abs(l.sum())) if l.sum() != 0 else 999.0
    med = float(np.median(l)) if len(l) else 0.0
    p90 = float(np.percentile(l, 10)) if len(l) else 0.0   # p90 loss = 10th pctile signed
    ex = (f"avg {p.mean():+.2f} pts | median loss ${med:,.0f} | p90 loss ${p90:,.0f} | "
          f"{len(l)} losers")
    if len(u) < 30:
        ex += " | n<30 CURIOSITY"
    return dict(window=window, entry=entry, n=int(len(u)),
                winRate=round(float((u > 0).mean()), 4),
                avgUsd=round(float(u.mean()), 2),
                profitFactor=round(min(pf, 999.0), 3),
                worstUsd=round(float(u.min()), 2),
                note=(note + " | " + ex).strip(" |"))


# ------------------------------------------------------------ self-check
def selfcheck():
    # one bar 100.00-100.50 vol 3 -> 1.0 per tick on 3 rows; POC tie -> upper
    lo = np.array([100.0, 100.25]); hi = np.array([100.5, 100.25]); v = np.array([3.0, 10.0])
    val, vah, poc = value_area(lo, hi, v)
    assert poc == 100.25, poc                       # 1 + 10 = 11 is the heaviest row
    assert (val, vah) == (100.25, 100.25), (val, vah)  # 11/13 >= 70% -> POC alone
    lo = np.array([10.0]); hi = np.array([11.0]); v = np.array([5.0])   # 5 rows, 1 each
    val, vah, poc = value_area(lo, hi, v)
    assert (val, vah, poc) == (10.0, 10.75, 10.0), (val, vah, poc)  # 4/5 rows, ties walk up
    # resolution: stop before target
    L = np.array([10.0, 8.0, 20.0]); H = np.array([11.0, 9.0, 21.0]); C = H
    xp, xi, st = resolve(L, H, C, 0, 8.5, 20.5)
    assert st == "stop" and xi == 1 and abs(xp - 8.0) < 1e-9, (st, xi, xp)
    # same bar both -> stop
    L = np.array([8.0]); H = np.array([21.0])
    assert resolve(L, H, H, 0, 8.5, 20.5)[2] == "stop"
    # no touch -> unresolved at last close
    L = np.array([10.0, 10.0]); H = np.array([11.0, 11.0]); C = np.array([10.5, 10.75])
    assert resolve(L, H, C, 0, 5.0, 30.0) == (10.75, 1, "unresolved")
    _, u = usd(100.0, 105.0)
    assert abs(u - (5 * 20 - 5.76)) < 1e-9
    print("selfcheck OK")


# ------------------------------------------------------------ leak check
def leakcheck(W=10):
    """Empirical no-lookahead audit on real data.
    1) VA is a pure function of the build slice -> re-running it with the arm bars
       appended must give the same VAL/VAH.
    2) break_low at the entry is a PREFIX minimum, not the whole-arm minimum.
    3) every event index lives in arm coordinates (>= the build window end)."""
    D = load_rth()
    keep = np.isin(D["day"], np.unique(D["day"])[:40])
    D = {k: v[keep] for k, v in D.items()}
    ev, _, _ = scan(W, D)
    strict = same = 0
    for e in ev:
        L, fb = e["L"], e["fb"]
        full_min = float(L[fb:].min())
        for i in (e["i_tick"], e["i_close"]):
            if i is None:
                continue
            assert i >= fb >= 0, (i, fb)
            b = brklow(e, i)
            assert abs(b - float(np.min(L[fb:i + 1]))) < 1e-12
            assert b >= full_min - 1e-12
            if b > full_min + 1e-12:
                strict += 1
            else:
                same += 1
        if e["i_tick"] is not None and e["i_close"] is not None:
            assert e["i_tick"] <= e["i_close"], (e["i_tick"], e["i_close"])
    # VA purity: recompute with a corrupted tail appended; result must not move
    ts, sod, day = D["ts"], D["sod"], D["day"]
    bnd = np.nonzero(np.r_[True, day[1:] != day[:-1], True])[0]
    a, z = bnd[0], bnd[1]
    bs, be = np.searchsorted(sod[a:z], [OPEN_SOD, OPEN_SOD + W * 60])
    ref = value_area(D["low"][a:z][bs:be], D["high"][a:z][bs:be], D["volume"][a:z][bs:be])
    lo2 = np.r_[D["low"][a:z][bs:be], D["low"][a:z][be:be + 500] - 500]
    hi2 = np.r_[D["high"][a:z][bs:be], D["high"][a:z][be:be + 500] - 500]
    v2 = np.r_[D["volume"][a:z][bs:be], D["volume"][a:z][be:be + 500] * 50]
    assert value_area(lo2[:be - bs], hi2[:be - bs], v2[:be - bs]) == ref
    assert value_area(lo2, hi2, v2) != ref, "corruption had no effect -> test is blind"
    print(f"leakcheck OK on {len(ev)} events / 40 sessions: "
          f"break_low strictly above the full-arm min in {strict} of {strict+same} "
          f"entries (a whole-arm min would give 0)")


# ------------------------------------------------------------ main
if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        selfcheck(); sys.exit(0)
    if "--leakcheck" in sys.argv:
        leakcheck(); sys.exit(0)
    D = load_rth()
    nsess = len(np.unique(D["day"]))
    print(f"RTH rows {len(D['ts']):,}  sessions {nsess}  "
          f"{D['day'][0]} .. {D['day'][-1]}")
    report = {}
    for W in (10, 30):
        ev, n_win, _ = scan(W, D)
        n_tick = sum(1 for e in ev if e["i_tick"] is not None)
        n_close = sum(1 for e in ev if e["i_close"] is not None)
        both = sum(1 for e in ev if e["i_tick"] is not None and e["i_close"] is not None)
        only = n_tick - both
        o, unres, misses, rh, rn = run_variants(W, ev)
        rows = [
            stats(f"W={W}", "CLOSE (30s close, no slip)", o["close"],
                  f"unresolved {unres['close']}"),
            stats(f"W={W}", "CLOSE +1 tick", o["close1t"], f"unresolved {unres['close1t']}"),
            stats(f"W={W}", "STOPMKT (all E_tick, +2t slip)", o["stopmkt_all"],
                  f"unresolved {unres['stopmkt_all']}"),
            stats(f"W={W}", "STOPMKT (E_close subset only)", o["stopmkt_sub"],
                  f"same-event comparison vs CLOSE; unresolved {unres['stopmkt_sub']}"),
            stats(f"W={W}", "LIMIT @VAL touch-fill", o["limit_touch"],
                  f"misses {misses['limit_touch']}/{n_close}; unresolved {unres['limit_touch']}"),
            stats(f"W={W}", "LIMIT @VAL through-fill", o["limit_through"],
                  f"misses {misses['limit_through']}/{n_close}; unresolved {unres['limit_through']}"),
            stats(f"W={W}", "E_tick-only (no 30s close)", o["tickonly"],
                  f"the cost of dropping bar-close confirmation; unresolved {unres['tickonly']}"),
        ]
        report[W] = dict(windows=n_win, breaks=len(ev), e_tick=n_tick, e_close=n_close,
                         overlap=both, tick_only=only, close_only=n_close - both,
                         retest=(rh, rn), rows=rows)
        print(f"\n=== W={W} ===")
        print(f"windows built {n_win}  windows with a VAL break {len(ev)}")
        print(f"E_tick {n_tick}  E_close {n_close}  overlap {both}  "
              f"E_tick-only {only}  E_close-only {n_close - both}")
        print(f"retest of VAL before CLOSE trade resolves: {rh}/{rn} = "
              f"{rh/rn:.1%}" if rn else "no E_close events")
        for r in rows:
            print(f"  {r['entry']:<34} n={r['n']:<5} win={r['winRate']:.1%} "
                  f"avg=${r['avgUsd']:>8,.2f} pf={r['profitFactor']:.3f} "
                  f"worst=${r['worstUsd']:>10,.2f}  [{r['note']}]")
    with open("/home/javlo/Code Projects/main-project/projects/Trading/GammaFlip/out/"
              "valuearea_B/result.json", "w") as f:
        json.dump({str(k): v for k, v in report.items()}, f, indent=1, default=float)
