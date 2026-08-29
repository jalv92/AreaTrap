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
