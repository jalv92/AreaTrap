# Source strategy — what the video actually says

Reference material, frozen. This file records the idea AreaTrap starts from.
It is not the project spec: the spec is `docs/design.md`, written from Javier's
own implementation, and it is allowed to diverge from everything below.

Source: <https://www.youtube.com/shorts/-j5xykuSD3I> — a LuxAlgo short, 3:00,
watched 2026-08-29. Two distinct things live in that video and conflating them
is the first way to get this project wrong.

## 1. The trader's method (0:00–0:47) — this is what AreaTrap is about

Attributed to Fabio, described as a four-time top-three finisher in the World
Trading Competition, trading **real order flow**. In his own words:

> I wait for the market to show its hand first and it always does. Draw the
> volume profile over the session. Shows you where the most money traded.
> That's the value area. Now the trade. Price drops below the value area low.
> Second it does, your eyes go on the volume bars. If they are declining,
> nobody really selling and the money isn't following price lower. Then the
> buyers step in and form a wall the sellers can't push through. There's the
> bubble, sellers getting absorbed. Price gets pulled back towards the money.
> When it closes back inside the value area and the volume bars grow again,
> that's your long. Stop below the low, target the value area high, right back
> into the money.

As a sequence, long side:

| # | Condition | What it is meant to prove |
|---|-----------|---------------------------|
| 1 | Volume profile over the session defines the value area (VAL / POC / VAH) | Where the size actually traded |
| 2 | Price trades **below the value area low** | The break that everyone else is chasing |
| 3 | Volume bars **declining** while below | The break has no participation — the money is not following price down |
| 4 | Buyers form a **wall** the sellers cannot get through; sellers are **absorbed** | Someone with size is taking the other side |
| 5 | Price **closes back inside** the value area **and** volume grows again | The break is confirmed failed and participation returns |
| 6 | Enter long. **Stop below the low.** **Target the value area high.** | Reversion "back into the money" |

Short side is never stated in the video; the symmetric reading is a break above
VAH on declining volume, sellers absorbing, close back inside, target VAL.

## 2. The indicator built on top of it (0:47–3:00) — NOT what AreaTrap is about

The presenter says plainly that he cannot replicate the method, because Fabio
trades real order flow and TradingView has none. What he ships instead changes
the premise in three places:

- **Previous day's value area, not the live session's.** His stated reason: a
  real-time volume profile moves the value area around too much. This is the
  large change, not a detail — it replaces a developing reference with a fixed
  one.
- **Absorption is replaced by an engulfing candle.** A candle pattern stands in
  for the order-flow event that the whole idea rests on.
- **Targets become VAL / POC / discretionary**, not the far side of the area.

He reports it works "really well" on the **Nasdaq 15-minute** chart and admits
signals do not appear every day. It is published as *Value Area Reversion
Signals* in the LuxAlgo library.

## 3. What the video does not contain

No win rate, no profit factor, no trade count, no sample period, no drawdown —
for either the method or the indicator. The evidence shown is two hand-picked
examples that worked. Every number this project ever reports has to come from
its own measurement.

## 4. The open problem this project inherits

Step 4 — absorption at a wall — is the step that distinguishes this from any
other failed-breakout pattern, and it is the step that needs real order flow.
NinjaTrader 8 does give access to what TradingView does not (Level II via
`OnMarketDepth`, tick-by-tick via `OnMarketData`), which is why this is an NT8
project and not a TradingView one. But see `[[nt8-heatmap-mbo-vendor-stack]]`
and `[[es-resting-order-size-measured]]` in workspace memory before assuming
what the depth feed can and cannot prove.
