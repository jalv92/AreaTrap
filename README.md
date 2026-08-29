<h1 align="center">AreaTrap</h1>

<p align="center">
  <b>A NinjaTrader 8 strategy for the failed break of the volume-profile value area — the move that leaves the crowd short below the low while the size quietly takes the other side.</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/status-design%20%C2%B7%20no%20code%20yet-red?style=flat-square" alt="status: design, no code yet">
  <img src="https://img.shields.io/badge/platform-NinjaTrader%208-blue?style=flat-square" alt="platform: NinjaTrader 8">
  <img src="https://img.shields.io/badge/language-C%23-purple?style=flat-square" alt="language: C#">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="license: MIT">
</p>

---

> ### Status
>
> **There is no strategy here yet.** This repository currently holds the source
> idea and nothing else. No code, no backtest, no measured edge, no claim of
> one. Futures trading carries substantial risk of loss; nothing published here
> should be run with money.

---

## The idea

Price breaks out of the session's value area — below the value area low, say —
and the volume that should confirm the break does not show up. Bars shrink.
Nobody is really selling. Resting bids absorb what does come, price gets pulled
back toward where the size traded, and it closes back inside the area with
volume returning. That close is the entry: stop under the low of the break,
target the far side of the value area.

The trade is not "buy the dip". It is the failed break: the reference is the
volume profile, and the filter that makes it a trade rather than a pattern is
the absence of participation in the break followed by its return.

The full statement of the idea, its source, and the parts of it that need real
order flow are in **[`docs/source-strategy.md`](docs/source-strategy.md)**.

## Why NinjaTrader 8

The step that carries the idea is absorption at a resting wall, and that needs
order flow. NT8 exposes tick-by-tick trades and Level II depth to a strategy
(`OnMarketData` / `OnMarketDepth`), which is what makes a mechanical version of
this worth attempting at all.

## Layout

```
docs/         design and reference documents
ninjascript/  the NT8 C# sources (deployed to NinjaTrader 8/bin/Custom/Strategies)
tests/        dotnet assert harness for the pure decision logic — no NT8 needed
```

## License

MIT. See [LICENSE](LICENSE).
