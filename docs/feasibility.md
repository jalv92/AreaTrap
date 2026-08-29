# Feasibility — what was verified, and what it costs the idea

Run 2026-08-29: six probes against the real NT8 install, each followed by an
independent adversarial checker told to refute it. Everything below survived
that second pass; claims the checkers knocked down are recorded at the bottom so
they do not get re-derived later.

The API claims were verified by **compiling probes with `nt8c` against this
machine's NinjaTrader DLLs**, not by reading documentation.

## 1. Buildable — all of it

| Piece | Verdict |
|---|---|
| Per-price volume with bid/ask split | `AddVolumetric(...)` in `State.Configure`; `BarsArray[n].BarsType as VolumetricBarsType` → `VolumetricData[] Volumes` with `GetBidVolumeForPrice` / `GetAskVolumeForPrice` / `GetTotalVolumeForPrice` / `GetDeltaForPrice`. Compiles clean. |
| 30-second base | `BarsPeriodType.Second` (= 3) is a legal volumetric base. Verified. |
| Absorption primitive | `VolumetricData.DeltaSl` — delta since price last touched the bar low. Purpose-built for exactly the step this strategy needs. |
| The right-ladder chart | A **Strategy** can override `OnRender` and `OnRenderTargetChanged`. A full probe — histogram loop, three `Stroke` levels, translucent band, right-anchored label — compiled inside a `Strategy` subclass. One file, no companion indicator. |

Two API facts that constrain the build:

- **`Volumes[]` takes an ABSOLUTE bar index** (`CurrentBars[n]`), never a barsAgo
  offset. Getting this wrong reads the wrong bar silently.
- **The chart repaints on a ~250 ms timer**, so a "live" profile refreshes at
  about 4 fps no matter the tick rate. `ForceRefresh()` only re-queues into the
  same timer. Worth knowing before promising real-time.

## 2. We write our own value area, and the rule is a real choice

NT8's free `@VolumeProfile.cs` computes no POC/VAH/VAL at all — it only bins and
draws. `NinjaTrader.NinjaScript.Indicators.MarketProfile` is public and
instantiable, but `CalculateValueArea()`, `Seal()` and `GetVolumeForLevel()` are
private/internal — proven by compile failure (CS1061), not assumed — and
`NinjaTrader.Vendor.dll` is AgileDotNet-obfuscated, so NT8's own loop is not
recoverable.

So the expansion rule is ours to pick, and the industry is genuinely split:

- **2-vs-2** rows per iteration — CBOT / *Mind Over Markets*, the canonical rule.
  **This is what `docs/mockups/chart-looks.html` uses.**
- **1-vs-1** — CQG, TradingView, and both implementations already on this
  machine (`TrapFlowCore.cs`, `IBBreakoutStrategy.cs`).

They produce different VAH/VAL on the same histogram. Also unsettled and not
cosmetic: both local implementations store rows **sparsely** (untraded prices
absent). Keeping untraded prices as zero rows changes VAL/POC/VAH on a gappy
profile — demonstrated on a worked example.

## 3. Measured on the real tape — 439 RTH sessions

Source: `projects/Trading/NQData/NQ_continuous_1s.npz`, 2024-11-22 → 2026-08-04.
Costs assumed $5.76 RT + 2 ticks slippage = $15.76.

**Two of the concerns raised before measuring were wrong:**

- **Value-area width is a non-issue.** Median 21 points on a 10-minute window
  against 0.79 points of cost. The earlier worry that a short-window area cannot
  clear costs does not survive contact with the data.
- **No regime dependency.** Balance days PF 0.891, trend days PF 0.862. It loses
  equally in both, so "it needs a balanced market" is not the explanation.

**What did hold, and it is the finding that matters:**

- **The raw geometric setup loses in all six configurations tested** —
  PF 0.857–0.968, average −$6.90 to −$37.49 per trade, n = 1,403–6,046.
- **The value area itself contributes nothing measurable.** A band of identical
  width overlapping the real area by only 25% performs indistinguishably
  (|t| < 0.7 on the difference). The profile is not adding information over a
  crude neighbouring band.
- **"Stop beyond the break extreme" is unbounded risk**: median $275, p90 $790,
  max $5,425 on a single contract — and it breaches a Lucid 50K daily loss limit
  in **26.8% of sessions**. This rule cannot ship as written.
- **VAH/VAL are stable on short windows; the POC is not.** Split-half
  disagreement is ~4–8 ticks for the value-area edges at every aggregation, while
  the POC row holds only ~1.5% of window volume at one tick per level and its
  position wanders. **The POC should not be used as a level on a 10-minute
  window.** The edges are the usable output.

### What was NOT tested

The three volume filters — declining volume into the break, absorption at the
extreme, volume returning on the close back inside — were never implemented. So
what is measured is *the geometry alone*, which is not Fabio's method; the
filters are the method. An adversarial checker specifically knocked down the
claim that absorption cannot be measured historically: via volumetric bid/ask
volume it **is** measurable, over roughly 70% of the local 439-session tape.

**The filters are untested, not untestable. That is the open question this
project exists to answer.**

## 4. Playback: traps and inventory

- `Core.Globals.Now` does **not** follow the Playback clock. Drive all window
  timing off `Time[0]`.
- A Playback rewind or reload re-feeds already-loaded bars through
  `OnBarUpdate` flagged `State.Realtime`. Guard with `CurrentBar >= Count - 2`
  or the profile double-counts.
- `Calculate.OnEachTick` is required. `OnPriceChange` drops volume updates at the
  same price, per the docs.
- Never submit orders from `OnMarketData` / `OnMarketDepth` — observed as
  `SQLite error (21): bind on a busy prepared statement [INSERT INTO
  Strategy2Order]`. Set a one-slot volatile flag, act in `OnBarUpdate`.
- **Replay inventory: 60 of 62 local NQ 09-26 days are usable.** Only 20260611
  (37,819 contracts) and 20260612 (93,361) are genuinely degraded against a
  ~495k median. Five Sunday reopens and two holiday early closes are legitimately
  thin and must not be discarded as corrupt.
- Historical bid/ask tick data is thin: 30 days for NQ 09-26 (2026-06-28 →
  2026-07-31) versus 68 days of Last. ES has none at all.

## 5. Absorption has to be defined as concentration, not as a wall

Measured on NQ: best-bid size p50 = **2 contracts**, dominant-wall p90 = 8, a
≥60-contract wall present in 0.23% of rows, and ~93% of the tape is 1-lots.
There is no wall on NQ to absorb against in the sense the video describes. Any
workable definition has to be **volume or delta concentration at the extreme
while price fails to advance**, which is what `DeltaSl` measures directly.

## Claims that did not survive their checker

Kept so they are not re-derived:

- "The placebo band sits at no volume node" — false; it holds ~30% of its own
  window's volume and overlaps the real area by 25%. The correct statement is
  that the two are statistically indistinguishable, not that a null location wins.
- "The frozen area is worse than random" — the benchmark was a band centred on
  the last traded price, which is an informed predictor, not a random location.
- "The level decays within ~3 minutes" — holds only at the 30-minute setting; the
  10-minute buckets run the opposite way (later entries better). Candidate
  artifact, not a lever.
- "POC split-half disagreement falls to 0 ticks at 8 ticks/level" — did not
  replicate (independent run: 8 ticks). Expose `ticksPerLevel` as a parameter,
  but do not justify a default with that number.

---

# Entry verification — 439 sessions, measured twice and adjudicated

Run 2026-08-29 after the above. Two agents implemented the SAME measurement spec
independently; a third reimplemented both, reproduced each one exactly, and ruled
on every disagreement. Scripts in `research/`. The adjudicated one runs in ~2 min.

## The question

Is the entry the lever? Specifically: does a resting limit at the value area low,
or a stop-market at VAL + 1 tick, beat the video's rule of entering on the 30-second
close back inside the area?

## Adjudicated results

Costs $5.76 round turn, 2 ticks slippage on the stop-market entry and on every stop
exit, none on the VAH target or the VAL limit fill.

| W | Variant | n | win | avg $ | PF |
|---|---|---|---|---|---|
| 10 | CLOSE (the video's rule) | 8,425 | 42.0% | −13.79 | 0.894 |
| 10 | STOPMKT at VAL+1t, full tradable set | 9,575 | 24.7% | −11.39 | 0.886 |
| 10 | LIMIT @VAL touch-fill | 6,233 | 28.8% | −16.80 | 0.853 |
| 10 | LIMIT @VAL through-fill | 6,137 | 27.7% | −21.84 | 0.812 |
| 30 | CLOSE | 2,791 | 34.8% | −19.65 | 0.902 |
| 30 | STOPMKT at VAL+1t, full tradable set | 2,989 | 21.5% | −8.43 | 0.940 |
| 30 | LIMIT @VAL touch-fill | 2,385 | 26.5% | −9.68 | 0.941 |
| 30 | LIMIT @VAL through-fill | 2,356 | 25.6% | −14.05 | 0.916 |

**Every tradable cell loses.** Changing the entry does not rescue this.

## What the measurement did establish

- **Retest rate: 74.0% at W=10 (6,233/8,425), 85.5% at W=30 (2,385/2,791).** Both
  runs agree on the numerator exactly. The limit branch is not starved for trades.
- **But its fills are adversely selected by construction, not by luck.** Entry sits
  above VAL and the stop below it, so *every* trade that stops out passes through
  VAL on the way down — the limit fills on 100% of the losers and only on some of
  the winners. The honest through-fill convention removes 96 of the touch-fill
  trades at W=10 and those 96 were net winners.
- **The reclaim-tick pokes that never close back inside are catastrophic**: n=1,153
  at W=10 winning 0.6% at −$161.51 average; n=198 at W=30 winning 0.5% at −$247.38.

## What it did NOT establish, recorded so it is not repeated

- **That the bar close "is a filter doing real work."** That framing came from
  comparing the stop-market restricted to events that closed back inside — which
  conditions on a future event and is not tradable. On the honest full set the two
  entries are within noise: PF 0.886 vs 0.894 at W=10, and the stop-market is
  *better* at W=30 (0.940 vs 0.902).
- **That W=30 limit touch-fill is "the least-bad variant."** It is PF 1.015 in the
  first half of the sample and 0.875 in the second. Split-half ranking only.
- **Any confidence interval at all.** Trades cluster at ~19/session (W=10) and
  ~6.8/session (W=30); no run produced a block bootstrap. PF 0.886 vs 0.940 across
  window sizes is NOT established as a real difference.
- **Fill realism.** The cost model is unvalidated against real NQ fills, and the
  limit branch assumes queue presence at VAL with no partial-fill modelling.

## Leaks found in the two implementations

Both were found by adjudication, neither by the scripts' own leak checks.

- Run B let the reclaim tick fire **on the break bar itself** — 72.0% of its
  stop-market population at W=10. Not executable: the order cannot be resting
  during the second the break first happens. This single bug produced B's
  PF 0.728, and is why the tradable number is 0.886.
- Run B's `break_low` included the entry bar's own low on an intrabar fill, which
  made its stop structurally unreachable on that bar — 83 target-wins and zero
  stops, ever, on entry bars.
- Run A resolved an intrabar fill from the *next* bar, so a second that fills you
  and then runs your stop was invisible to it.
- Run A marked unresolved trades at the entry price instead of the market, which
  is where its "exactly zero winners in 198" came from. It is one winner.

## NT8 order mechanics, verified against the install

Settled by parsing the `.resources` tables out of `NinjaTrader.Core.dll` and
decompiling `NinjaTrader.Cbi.Simulator`, not by reading forum posts.

- **A buy limit above the market is REJECTED, not filled.** `CbiSimulatorSubmit10`:
  *"Limit price can't be greater than current ask."* An earlier claim in this
  project that such an order fills instantly at the market was wrong.
- **And the historical path has no such check.** The entire assembly contains no
  wrong-side-limit error outside the sim broker, so **the same code can fill in the
  Strategy Analyzer and be rejected in Sim101 or live.** Any limit entry has to
  guard its own side rather than trust the platform to.
- **A buy stop must rest above the market** (`CbiSimulatorSubmit6`), which is what
  a stop-market at VAL + 1 tick placed while price is below VAL is. Valid.
- **`IsFillLimitOnTouch`** exists and historical limit fills penetrate by default,
  with no volume or queue condition — a backtest knows only OHLC. Limit entries are
  where the backtest flatters you most.
- Under Standard fill resolution a market order fills at the **next primary bar's
  open**, which is the artifact already measured in this workspace ($4,093 tick-true
  reported as $17,777).
