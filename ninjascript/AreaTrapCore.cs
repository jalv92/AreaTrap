// AreaTrapCore.cs — the whole decision, and none of NinjaTrader.
//
// ZERO `using NinjaTrader.*`, own namespace, C# 7.3 only, no I/O and no clock of
// its own. Everything here runs under `dotnet run --project tests` without
// NinjaTrader installed, which is the only reason any of it can be tested.
//
// WHAT THIS IS FOR. The strategy it serves is not a bet that the setup works —
// 439 sessions say the geometry alone loses (PF 0.86-0.97, see docs/feasibility.md).
// It is an instrument built to answer the one question nobody has measured: do the
// three volume filters the trader actually names — participation draining out of
// the break, size absorbing at the extreme, participation returning on the close
// back inside — separate the winners from the losers? So the filters are not a
// gate bolted on the side. They are the subject, and AtTelemetry below is the
// product: every reclaim is scored against ALL EIGHT filter combinations at once,
// including the empty one, so a single Playback session answers "what did this
// filter reject, and would it have lost?" instead of producing one more curve.
//
// THREE THINGS THE MEASUREMENT ALREADY SETTLED, encoded here rather than left as
// dials someone has to rediscover:
//
//   * The POC is NOT a level. On a 10-minute window its row carries ~1.5% of the
//     volume and its position wanders 5+ ticks between resampled halves, while
//     VAH/VAL move only ~4-8. It is computed because the value area needs it as a
//     seed, and it is reported for drawing, but nothing trades off it.
//   * The stop is CAPPED. "Below the extreme of the break" unbounded produced a
//     $5,425 worst case on one contract and breached a Lucid 50K daily limit in
//     26.8% of sessions. MaxStopPoints is not a preference.
//   * EntryMode is NOT settled by measurement, and this comment used to claim it
//     was. The pokes that fire on the reclaim tick and never close back inside are
//     genuinely catastrophic (-$161 / -$247 average, 0.5-0.6% win rate), but the
//     full TRADABLE stop-market set that contains them still nets out no worse
//     than waiting for the close -- W=10: -$11.39 (PF 0.886) against the close's
//     -$13.79 (PF 0.894); W=30: -$8.43 (PF 0.940) against -$19.65 (PF 0.902) --
//     because the earlier fill price pays for them. The two entries are within
//     noise of each other and no profit factor in that study carries a confidence
//     interval, so BarClose is the default because it is the rule the video states
//     and the one the mockup draws. A convention, not a finding. ReclaimTick is
//     there to be measured against it, which is what the telemetry is for.
//
// THE VALUE-AREA RULE IS A CHOICE, NOT A CONSTANT. NinjaTrader's own
// CalculateValueArea() is private inside an obfuscated assembly, so there is
// nothing to copy. The industry is split: the CBOT / "Mind Over Markets" rule
// takes a PAIR of rows per side per iteration; CQG, TradingView and both
// implementations already in this workspace take ONE. They produce different
// VAH/VAL on the same histogram. ExpandTwoRows exposes the choice and defaults to
// the 1-vs-1 rule the 439-session measurement used, so the code and the numbers
// in docs/feasibility.md describe the same object.
using System;
using System.Collections.Generic;

namespace AreaTrapCore
{
    public enum AtPhase
    {
        Building = 0,   // the window is open, the profile is still moving
        Armed    = 1,   // value area frozen, hunting the break-and-reclaim
        InTrade  = 2    // position open; the strategy owns the exit
    }

    public enum AtSide { None = 0, Long = 1, Short = -1 }

    public enum AtAction { None = 0, EnterLong = 1, EnterShort = 2 }

    public enum AtEntryMode
    {
        BarClose    = 0,   // the video's rule, and the measured filter
        ReclaimTick = 1    // enter at the level the instant it is reclaimed
    }

    // ------------------------------------------------------------------ input
    // A bar as the core wants it. The strategy fills BuyVol/SellVol from the
    // volumetric ladder when it has one; without it they may be zero, and the
    // absorption filter simply never passes, which is the honest failure.
    public struct AtBar
    {
        public DateTime Time;
        public double Open, High, Low, Close;
        public long Volume, BuyVol, SellVol;

        public AtBar(DateTime t, double o, double h, double l, double c,
                     long v, long buy, long sell)
        {
            Time = t; Open = o; High = h; Low = l; Close = c;
            Volume = v; BuyVol = buy; SellVol = sell;
        }
    }

    // ---------------------------------------------------------------- profile
    // Volume at price, SPARSE: a price that never traded has no row. Both
    // implementations already in this workspace (TrapFlowCore, IBBreakoutStrategy)
    // are sparse too, and the difference is not cosmetic — keeping untraded rows
    // as zeros changes VAH/VAL on a gappy profile, because the expansion walks
    // over them and spends its budget on nothing.
    public sealed class AtProfile
    {
        private readonly Dictionary<long, long> _bid = new Dictionary<long, long>();
        private readonly Dictionary<long, long> _ask = new Dictionary<long, long>();
        private readonly Dictionary<long, long> _tot = new Dictionary<long, long>();

        public double TickSize = 0.25;
        public int TicksPerLevel = 1;
        public long TotalVolume { get; private set; }
        public int RowCount { get { return _tot.Count; } }

        public double RowSize { get { return TickSize * TicksPerLevel; } }

        public void Clear()
        {
            _bid.Clear(); _ask.Clear(); _tot.Clear();
            TotalVolume = 0;
        }

        public long RowOf(double price)
        {
            return (long)Math.Floor(price / RowSize + 1e-9);
        }

        public double PriceOf(long row) { return row * RowSize; }

        public void Add(double price, long volume, long buyVol, long sellVol)
        {
            if (volume <= 0) return;
            long r = RowOf(price);
            long cur;
            _tot.TryGetValue(r, out cur); _tot[r] = cur + volume;
            if (buyVol > 0) { _ask.TryGetValue(r, out cur); _ask[r] = cur + buyVol; }
            if (sellVol > 0) { _bid.TryGetValue(r, out cur); _bid[r] = cur + sellVol; }
            TotalVolume += volume;
        }

        // Spread one bar's volume across the rows it traded through. This is the
        // honest approximation when only OHLCV is available; when the strategy has
        // a volumetric ladder it calls Add() per real price instead and this is
        // never used.
        public void AddBarSpread(AtBar b)
        {
            if (b.Volume <= 0) return;
            long lo = RowOf(b.Low), hi = RowOf(b.High);
            if (hi < lo) { long t = lo; lo = hi; hi = t; }
            long n = hi - lo + 1;
            if (n <= 0) return;
            long share = b.Volume / n, rem = b.Volume - share * n;
            long buyShare = b.BuyVol / n, sellShare = b.SellVol / n;
            for (long r = lo; r <= hi; r++)
            {
                long v = share + (r - lo < rem ? 1 : 0);
                long cur;
                _tot.TryGetValue(r, out cur); _tot[r] = cur + v;
                if (buyShare > 0) { _ask.TryGetValue(r, out cur); _ask[r] = cur + buyShare; }
                if (sellShare > 0) { _bid.TryGetValue(r, out cur); _bid[r] = cur + sellShare; }
                TotalVolume += v;
            }
        }

        public long VolumeAt(double price)
        {
            long v; _tot.TryGetValue(RowOf(price), out v); return v;
        }
        public long BidVolumeAt(double price)
        {
            long v; _bid.TryGetValue(RowOf(price), out v); return v;
        }
        public long AskVolumeAt(double price)
        {
            long v; _ask.TryGetValue(RowOf(price), out v); return v;
        }

        // Sorted occupied rows, low to high. Allocates; called once per freeze and
        // once per redraw, never per tick.
        public List<long> SortedRows()
        {
            List<long> rows = new List<long>(_tot.Keys);
            rows.Sort();
            return rows;
        }

        public long PeakVolume()
        {
            long peak = 0;
            foreach (long v in _tot.Values) if (v > peak) peak = v;
            return peak;
        }
    }

    // ------------------------------------------------------------- value area
    public struct AtValueArea
    {
        public bool Valid;
        public double Poc, Val, Vah;
        public double Coverage;     // the share actually enclosed; >= pct, never exactly it
        public int RowsInside;

        public double Width { get { return Vah - Val; } }
    }

    public static class AtValueAreaMath
    {
        // Start at the POC and repeatedly take the heavier side until the enclosed
        // volume reaches the target share. `twoRows` selects the CBOT pair rule;
        // the default single-row rule is what the 439-session measurement used.
        //
        // Ties expand UPWARD. An arbitrary rule is required and this one is the
        // conservative choice for a long: it lifts VAL (the entry reference) and
        // pulls VAH (the target) closer, so it cannot flatter a long result.
        public static AtValueArea Compute(AtProfile p, double pct, bool twoRows)
        {
            AtValueArea va = new AtValueArea();
            if (p == null || p.TotalVolume <= 0 || p.RowCount == 0) return va;

            List<long> rows = p.SortedRows();
            int n = rows.Count;
            long[] vol = new long[n];
            int pocIdx = 0;
            for (int i = 0; i < n; i++)
            {
                vol[i] = p.VolumeAt(p.PriceOf(rows[i]));
                if (vol[i] > vol[pocIdx]) pocIdx = i;
            }

            double target = p.TotalVolume * pct;
            int lo = pocIdx, hi = pocIdx;
            double inside = vol[pocIdx];
            int step = twoRows ? 2 : 1;

            while (inside < target && (lo > 0 || hi < n - 1))
            {
                long up = 0, dn = 0;
                for (int k = 1; k <= step; k++) if (hi + k < n) up += vol[hi + k];
                for (int k = 1; k <= step; k++) if (lo - k >= 0) dn += vol[lo - k];

                if (up >= dn && hi < n - 1)
                {
                    for (int k = 0; k < step && hi < n - 1; k++) { hi++; inside += vol[hi]; }
                }
                else if (lo > 0)
                {
                    for (int k = 0; k < step && lo > 0; k++) { lo--; inside += vol[lo]; }
                }
                else break;
            }

            va.Valid = true;
            va.Poc = p.PriceOf(rows[pocIdx]);
            va.Val = p.PriceOf(rows[lo]);
            va.Vah = p.PriceOf(rows[hi]);
            va.Coverage = inside / p.TotalVolume;
            va.RowsInside = hi - lo + 1;
            return va;
        }
    }

    // ----------------------------------------------------------------- config
    public sealed class AtConfig
    {
        public double TickSize = 0.25;
        public int TicksPerLevel = 1;

        public int WindowMinutes = 10;        // how long the profile builds
        public int ArmMinutes = 10;           // how long a frozen area may hunt before it is stale
        public double ValueAreaPercent = 0.70;
        public bool ExpandTwoRows = false;    // false = 1-vs-1, what the measurement used

        public AtEntryMode EntryMode = AtEntryMode.BarClose;

        // Filter dials. Each one is a single number on purpose: a filter with three
        // knobs cannot be swept honestly from one Playback session.
        public bool UseDecliningVolume = true;
        public double DecliningVolumeMax = 0.80;   // mean break-bar volume / median build-bar volume

        public bool UseAbsorption = true;
        public int AbsorptionMinBars = 2;          // bars the extreme has to survive
        public long AbsorptionMinDelta = 200;      // net sell-aggressor volume since the extreme

        public bool UseReturningVolume = true;
        public double ReturningVolumeMin = 1.00;   // reclaim-bar volume / median build-bar volume

        public double MinBreakPoints = 0.50;       // a break has to actually leave the area
        public double MaxBreakPoints = 0.0;        // 0 = no cap; beyond this the break is too deep to fade

        public double StopBufferPoints = 1.00;     // beyond the extreme
        public double MaxStopPoints = 12.0;        // the cap that keeps a bad day survivable
        public double MinAreaPoints = 0.0;         // 0 = off; measured as a non-issue on NQ

        public bool AllowLong = true;
        public bool AllowShort = true;
    }

    // -------------------------------------------------------------- telemetry
    // The product. Every reclaim is scored against all eight subsets of the three
    // filters at once — mask bit 0 declining, 1 absorption, 2 returning — so one
    // Playback session says what each filter rejected AND what that rejection was
    // worth. Mask 0 is the unfiltered geometry, which is the control arm.
    public sealed class AtCombo
    {
        public int Trades, Wins;
        public double GrossWin, GrossLoss;   // GrossLoss positive
        public double WorstPoints;

        public void Add(double points)
        {
            Trades++;
            if (points > 0) { Wins++; GrossWin += points; }
            else { GrossLoss += -points; if (-points > WorstPoints) WorstPoints = -points; }
        }
        public double WinRate { get { return Trades > 0 ? (double)Wins / Trades : 0.0; } }
        public double AvgPoints { get { return Trades > 0 ? (GrossWin - GrossLoss) / Trades : 0.0; } }
        public double ProfitFactor { get { return GrossLoss > 0 ? GrossWin / GrossLoss : (GrossWin > 0 ? double.PositiveInfinity : 0.0); } }
    }

    public sealed class AtTelemetry
    {
        public readonly AtCombo[] Combos = new AtCombo[8];
        public int Windows, Breaks, Reclaims, Entries, Expired;

        public AtTelemetry()
        {
            for (int i = 0; i < 8; i++) Combos[i] = new AtCombo();
        }

        public static int MaskOf(bool declining, bool absorption, bool returning)
        {
            return (declining ? 1 : 0) | (absorption ? 2 : 0) | (returning ? 4 : 0);
        }

        // A combo accepts an event only if every filter the combo requires passed.
        public void Record(int verdictMask, double points)
        {
            for (int mask = 0; mask < 8; mask++)
                if ((mask & verdictMask) == mask) Combos[mask].Add(points);
        }

        public static string ComboName(int mask)
        {
            if (mask == 0) return "none (raw geometry)";
            string s = "";
            if ((mask & 1) != 0) s += "declining+";
            if ((mask & 2) != 0) s += "absorption+";
            if ((mask & 4) != 0) s += "returning+";
            return s.Substring(0, s.Length - 1);
        }
    }

    // ------------------------------------------------------------- the engine
    public struct AtDecision
    {
        public AtAction Action;
        public double Entry, Stop, Target;
        public int VerdictMask;
        public string Reason;
    }

    // A candidate that was raised but may not have been taken. It is walked
    // forward on later bars purely so the telemetry can say what it would have
    // been worth. Counterfactual by construction: nothing here touches an order.
    internal sealed class AtShadow
    {
        public AtSide Side;
        public double Entry, Stop, Target;
        public int VerdictMask;
        public DateTime OpenedAt;
    }

    public sealed class AtEngine
    {
        public readonly AtConfig Cfg;
        public readonly AtProfile Profile = new AtProfile();
        public readonly AtTelemetry Telemetry = new AtTelemetry();

        public AtPhase Phase { get; private set; }
        public AtValueArea Area;
        public DateTime WindowStart { get; private set; }
        public DateTime ArmStart { get; private set; }

        private readonly List<long> _buildVolumes = new List<long>();
        private double _medianBuildVolume;

        // break state, valid only while Armed
        private bool _broke;
        private AtSide _breakSide;
        private double _breakExtreme;      // lowest low (long) / highest high (short)
        private int _breakBars;
        private long _breakVolSum;
        private int _barsSinceExtreme;
        private long _deltaSinceExtreme;   // sell - buy, so positive means sellers dominating

        private readonly List<AtShadow> _shadows = new List<AtShadow>();
        private bool _started;

        public AtEngine(AtConfig cfg)
        {
            if (cfg == null) throw new ArgumentNullException("cfg");
            Cfg = cfg;
            Profile.TickSize = cfg.TickSize;
            Profile.TicksPerLevel = cfg.TicksPerLevel;
            Phase = AtPhase.Building;
        }

        public double MedianBuildVolume { get { return _medianBuildVolume; } }
        public bool HasBreak { get { return _broke; } }
        public double BreakExtreme { get { return _breakExtreme; } }
        public int ShadowCount { get { return _shadows.Count; } }

        // Restart the cycle from this instant: a fresh window, nothing frozen.
        public void StartWindow(DateTime t)
        {
            Profile.Clear();
            _buildVolumes.Clear();
            _medianBuildVolume = 0;
            Area = new AtValueArea();
            Phase = AtPhase.Building;
            WindowStart = t;
            ClearBreak();
            _started = true;
        }

        // The strategy is the authority on the position. When NT8 says flat, the
        // cycle restarts here — which is the stated design: the next window begins
        // when the trade ends, not on the clock.
        public void OnTradeClosed(DateTime t)
        {
            if (Phase == AtPhase.InTrade) StartWindow(t);
        }

        private void ClearBreak()
        {
            _broke = false;
            _breakSide = AtSide.None;
            _breakExtreme = 0;
            _breakBars = 0;
            _breakVolSum = 0;
            _barsSinceExtreme = 0;
            _deltaSinceExtreme = 0;
        }

        public AtDecision OnBar(AtBar b)
        {
            AtDecision d = new AtDecision();
            d.Action = AtAction.None;

            if (!_started) StartWindow(b.Time);

            UpdateShadows(b);

            // A single bar can cross ONE phase boundary: the window can close on it
            // and the hunt begin on the same bar, or a stale arm can expire on it and
            // the next window open. Two passes covers both. Without this the bar that
            // ends the window was being added to the profile AND was the first bar of
            // the break it is supposed to be judged against -- a one-bar lookahead
            // hiding as an off-by-one.
            for (int pass = 0; pass < 2; pass++)
            {
                AtPhase before = Phase;
                if (Phase == AtPhase.Building) StepBuilding(b);
                else if (Phase == AtPhase.Armed) d = StepArmed(b);
                else break;                        // InTrade: the strategy owns the exit
                if (Phase == before) break;
            }
            return d;
        }

        private void StepBuilding(AtBar b)
        {
            // The window is [WindowStart, WindowStart + WindowMinutes). The bar that
            // reaches the boundary belongs to the HUNT, not to the profile it would
            // otherwise help build.
            bool elapsed = (b.Time - WindowStart).TotalMinutes + 1e-9 >= Cfg.WindowMinutes;
            if (!elapsed || Profile.TotalVolume <= 0)
            {
                Profile.AddBarSpread(b);
                _buildVolumes.Add(b.Volume);
                return;
            }

            Area = AtValueAreaMath.Compute(Profile, Cfg.ValueAreaPercent, Cfg.ExpandTwoRows);
            _medianBuildVolume = Median(_buildVolumes);
            Telemetry.Windows++;

            if (!Area.Valid || (Cfg.MinAreaPoints > 0 && Area.Width < Cfg.MinAreaPoints))
            {
                StartWindow(b.Time);     // unusable area, do not arm on it
                return;
            }
            Phase = AtPhase.Armed;
            ArmStart = b.Time;
            ClearBreak();
        }

        private AtDecision StepArmed(AtBar b)
        {
            AtDecision d = new AtDecision();
            d.Action = AtAction.None;

            // A frozen area stops describing anything eventually. Rebuilding beats
            // hunting a stale level, and the timeout is the only thing standing
            // between this design and a level from forty minutes ago.
            if ((b.Time - ArmStart).TotalMinutes + 1e-9 >= Cfg.ArmMinutes)
            {
                Telemetry.Expired++;
                StartWindow(b.Time);
                return d;
            }

            double val = Area.Val, vah = Area.Vah;

            // ---- long side: break below VAL, reclaim back above it
            if (Cfg.AllowLong)
            {
                if (b.Low < val - Cfg.MinBreakPoints)
                {
                    if (!_broke || _breakSide != AtSide.Long)
                    {
                        ClearBreak();
                        _broke = true; _breakSide = AtSide.Long; _breakExtreme = b.Low;
                        _barsSinceExtreme = 0; _deltaSinceExtreme = 0;
                    }
                    _breakBars++; _breakVolSum += b.Volume;
                    if (b.Low < _breakExtreme)
                    {
                        _breakExtreme = b.Low;
                        _barsSinceExtreme = 0;
                        _deltaSinceExtreme = 0;
                    }
                    else
                    {
                        _barsSinceExtreme++;
                        _deltaSinceExtreme += (b.SellVol - b.BuyVol);
                    }
                }
                else if (_broke && _breakSide == AtSide.Long && ReclaimedLong(b, val))
                {
                    return RaiseCandidate(b, AtSide.Long, val, vah);
                }
            }

            // ---- short side: mirror
            if (Cfg.AllowShort && d.Action == AtAction.None)
            {
                if (b.High > vah + Cfg.MinBreakPoints)
                {
                    if (!_broke || _breakSide != AtSide.Short)
                    {
                        ClearBreak();
                        _broke = true; _breakSide = AtSide.Short; _breakExtreme = b.High;
                        _barsSinceExtreme = 0; _deltaSinceExtreme = 0;
                    }
                    _breakBars++; _breakVolSum += b.Volume;
                    if (b.High > _breakExtreme)
                    {
                        _breakExtreme = b.High;
                        _barsSinceExtreme = 0;
                        _deltaSinceExtreme = 0;
                    }
                    else
                    {
                        _barsSinceExtreme++;
                        _deltaSinceExtreme += (b.BuyVol - b.SellVol);
                    }
                }
                else if (_broke && _breakSide == AtSide.Short && ReclaimedShort(b, vah))
                {
                    return RaiseCandidate(b, AtSide.Short, vah, val);
                }
            }

            return d;
        }

        private bool ReclaimedLong(AtBar b, double val)
        {
            if (Cfg.EntryMode == AtEntryMode.BarClose) return b.Close > val;
            return b.High >= val + Cfg.TickSize;
        }

        private bool ReclaimedShort(AtBar b, double vah)
        {
            if (Cfg.EntryMode == AtEntryMode.BarClose) return b.Close < vah;
            return b.Low <= vah - Cfg.TickSize;
        }

        // One reclaim, scored against every filter combination, taken only if the
        // configured filters all passed. The shadow is raised regardless — that is
        // the whole point: a filter that rejects a setup still has to answer for it.
        private AtDecision RaiseCandidate(AtBar b, AtSide side, double edge, double far)
        {
            AtDecision d = new AtDecision();
            d.Action = AtAction.None;

            Telemetry.Reclaims++;
            if (_breakBars > 0) Telemetry.Breaks++;

            double depth = side == AtSide.Long ? edge - _breakExtreme : _breakExtreme - edge;
            if (Cfg.MaxBreakPoints > 0 && depth > Cfg.MaxBreakPoints)
            {
                ClearBreak();
                d.Reason = "break too deep";
                return d;
            }

            bool fDecl = _medianBuildVolume > 0 && _breakBars > 0 &&
                         ((double)_breakVolSum / _breakBars) <= _medianBuildVolume * Cfg.DecliningVolumeMax;
            bool fAbs  = _barsSinceExtreme >= Cfg.AbsorptionMinBars &&
                         _deltaSinceExtreme >= Cfg.AbsorptionMinDelta;
            bool fRet  = _medianBuildVolume > 0 &&
                         b.Volume >= _medianBuildVolume * Cfg.ReturningVolumeMin;

            int verdict = AtTelemetry.MaskOf(fDecl, fAbs, fRet);

            double entry = Cfg.EntryMode == AtEntryMode.BarClose
                ? b.Close
                : (side == AtSide.Long ? edge + Cfg.TickSize : edge - Cfg.TickSize);

            double stop, target;
            if (side == AtSide.Long)
            {
                stop = _breakExtreme - Cfg.StopBufferPoints;
                if (entry - stop > Cfg.MaxStopPoints) stop = entry - Cfg.MaxStopPoints;
                target = far;
            }
            else
            {
                stop = _breakExtreme + Cfg.StopBufferPoints;
                if (stop - entry > Cfg.MaxStopPoints) stop = entry + Cfg.MaxStopPoints;
                target = far;
            }

            AtShadow s = new AtShadow();
            s.Side = side; s.Entry = entry; s.Stop = stop; s.Target = target;
            s.VerdictMask = verdict; s.OpenedAt = b.Time;
            _shadows.Add(s);

            int required = AtTelemetry.MaskOf(Cfg.UseDecliningVolume, Cfg.UseAbsorption, Cfg.UseReturningVolume);
            ClearBreak();

            if ((required & verdict) != required)
            {
                d.VerdictMask = verdict;
                d.Reason = "filtered";
                return d;
            }

            d.Action = side == AtSide.Long ? AtAction.EnterLong : AtAction.EnterShort;
            d.Entry = entry; d.Stop = stop; d.Target = target;
            d.VerdictMask = verdict;
            d.Reason = "reclaim";
            Phase = AtPhase.InTrade;
            Telemetry.Entries++;
            return d;
        }

        // Walk every open shadow forward one bar. Stop before target when a single
        // bar contains both: the bar carries no intrabar sequence, and booking the
        // target would be an optimism the data cannot support.
        private void UpdateShadows(AtBar b)
        {
            for (int i = _shadows.Count - 1; i >= 0; i--)
            {
                AtShadow s = _shadows[i];
                if (b.Time <= s.OpenedAt) continue;

                double points = 0;
                bool done = false;
                if (s.Side == AtSide.Long)
                {
                    if (b.Low <= s.Stop) { points = s.Stop - s.Entry; done = true; }
                    else if (b.High >= s.Target) { points = s.Target - s.Entry; done = true; }
                }
                else
                {
                    if (b.High >= s.Stop) { points = s.Entry - s.Stop; done = true; }
                    else if (b.Low <= s.Target) { points = s.Entry - s.Target; done = true; }
                }

                if (done)
                {
                    Telemetry.Record(s.VerdictMask, points);
                    _shadows.RemoveAt(i);
                }
            }
        }

        // Session end: settle whatever is still open at the last price so a day's
        // telemetry is not quietly missing its unresolved trades.
        public void SettleShadows(double lastPrice)
        {
            for (int i = 0; i < _shadows.Count; i++)
            {
                AtShadow s = _shadows[i];
                double points = s.Side == AtSide.Long ? lastPrice - s.Entry : s.Entry - lastPrice;
                Telemetry.Record(s.VerdictMask, points);
            }
            _shadows.Clear();
        }

        private static double Median(List<long> xs)
        {
            if (xs == null || xs.Count == 0) return 0;
            List<long> c = new List<long>(xs);
            c.Sort();
            int n = c.Count;
            return (n % 2 == 1) ? c[n / 2] : (c[n / 2 - 1] + c[n / 2]) / 2.0;
        }
    }
}
