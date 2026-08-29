# AreaTrap — design

The spec. It grows as decisions get made and it is allowed to contradict
`source-strategy.md`, which is frozen reference, not instruction.

Status: **the visual treatment is decided, the trade rules are not.**

## Decided

### D-01 · The chart look is the right ladder

Chosen 2026-08-29 from four studies rendered on identical bars
(`docs/mockups/chart-looks.html`, published as an artifact). What it commits us to:

- The volume profile is **pinned to a reserved gutter on the right edge**, and the
  price plot ends before that gutter starts. The profile never overlaps a candle.
  This is a real constraint on the renderer, not a style: the bar x-scale has to
  be computed against the reduced plot width, or the ladder eats the last bars —
  which is the bug the first draft of the mockup actually had.
- The value area is drawn as a **filled band projected across the whole plot**,
  with VAH / VAL solid and POC dashed, plus right-edge price tags.
- A **status block** states the machine's state in words — `BUILDING` with bars
  remaining, `ARMED`, `IN TRADE` with live R:R, `FLAT · REBUILDING`. The point is
  that a Playback session can be debugged by reading the chart instead of
  inferring state from shapes.
- Accepted cost: the ladder no longer lines up in time with the bars that built
  it. The link between window and area is knowledge, not geometry.

Rendering goes in `OnRender` on the strategy itself (confirmed available there),
not a companion indicator, so there is one file to deploy and one state to keep.

## Open — needed before any code

1. **The R:R problem.** Measured 0.90 on the reference cycle: entering on the
   close back inside costs the whole reclaim distance while the stop stays under
   the break low. Candidate fixes: cap qualifying break depth; rest a limit at
   the value area low instead of entering on the close; target beyond the far
   side. Not yet chosen.
2. **Cycle trigger** — rebuild the window on the clock, or after the trade
   closes. Javier's stated intent is after the trade, which drifts off the clock
   and leaves the strategy blind while rebuilding.
3. **Stale-area timeout** — how long a frozen area may stay armed before it is
   rebuilt unused.
4. **Minimum area width** — below some width the far side does not clear costs
   and the cycle should sit out.
5. **Absorption strictness**, and whether a bar-volume-only fallback exists for
   when order flow is unavailable.

## Constraints already established

- Absorption needs order flow. `OnMarketDepth` does not fire in historical and
  NinjaScript exposes no MBO (SFT-1496), so any absorption-gated version runs in
  **Playback / Market Replay and live only** — never the Strategy Analyzer.
- 30-second bars: `BarsPeriodType.Second`, `Value = 30`.
- Never submit orders from inside `OnMarketData` / `OnMarketDepth`.
