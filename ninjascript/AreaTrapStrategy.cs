// AreaTrapStrategy.cs — the NinjaTrader wiring, and nothing else.
//
// Every decision lives in AreaTrapCore.cs, which has zero NT8 references and is
// gated by `dotnet run --project tests`. This file only does the things the core
// deliberately cannot: read bars, read the volumetric ladder, submit orders, and
// print what happened. If a rule appears in this file, it is in the wrong file.
//
// WHAT IT IS FOR. This is an instrument, not a bet. 439 sessions say the geometry
// loses (docs/feasibility.md) and that changing the entry does not rescue it. The
// open question is whether the three volume filters separate winners from losers,
// so the strategy scores every reclaim against all eight filter combinations and
// prints the table. Read the table, not the equity curve.
//
// FOUR PLAYBACK TRAPS, all verified against this install, all handled below:
//
//   1. Core.Globals.Now does NOT follow the Playback clock. Every window boundary
//      here is measured off Time[0].
//   2. A Playback rewind or reload re-feeds already-loaded bars through
//      OnBarUpdate flagged State.Realtime. Feeding those to the core would
//      double-count the profile, so bars are gated on a strictly increasing
//      timestamp rather than on State.
//   3. Calculate must be OnEachTick. OnPriceChange drops volume updates at the
//      same price, which is exactly the data the profile is made of.
//   4. Orders must never be submitted from OnMarketData/OnMarketDepth. Nothing
//      here does; the core is driven from OnBarUpdate only.
//
// AND ONE ORDER TRAP THAT IS NOT IN ANY GUIDE. The sim broker rejects a buy limit
// above the ask ("Limit price can't be greater than current ask",
// CbiSimulatorSubmit10, read out of NinjaTrader.Core.dll) — but the HISTORICAL
// path carries no such check at all. The same code fills in the Strategy Analyzer
// and is rejected in Sim101 or live. So GuardedLimit() below refuses to place a
// limit on the wrong side itself rather than trusting the platform to be
// consistent about it.
#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel.DataAnnotations;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.BarsTypes;
using AreaTrapCore;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    public class AreaTrapStrategy : Strategy
    {
        private AtEngine _engine;
        private AtConfig _cfg;
        private DateTime _lastBarTime = DateTime.MinValue;
        private DateTime _sessionDate = DateTime.MinValue;
        private int _volSeries = -1;
        private bool _periodWarned;

        // Set before Enter*, cleared on the flat callback. NT8 delivers order and
        // execution events on their own thread and out of order with OnBarUpdate,
        // so the flag has to be raised BEFORE the entry call, never after.
        private bool _entryInFlight;

        #region Parameters
        [NinjaScriptProperty]
        [Display(Name = "Window minutes", Order = 1, GroupName = "01. Cycle")]
        public int WindowMinutes { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Arm minutes (stale timeout)", Order = 2, GroupName = "01. Cycle")]
        public int ArmMinutes { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Quantity", Order = 3, GroupName = "01. Cycle")]
        public int Quantity { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Value area percent", Order = 1, GroupName = "02. Value area")]
        public double ValueAreaPercent { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Ticks per level", Order = 2, GroupName = "02. Value area")]
        public int TicksPerLevel { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Expand two rows (CBOT rule)", Order = 3, GroupName = "02. Value area")]
        public bool ExpandTwoRows { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Entry on bar close (else reclaim tick)", Order = 1, GroupName = "03. Entry")]
        public bool EntryOnBarClose { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Min break points", Order = 2, GroupName = "03. Entry")]
        public double MinBreakPoints { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Max break points (0 = off)", Order = 3, GroupName = "03. Entry")]
        public double MaxBreakPoints { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Longs", Order = 4, GroupName = "03. Entry")]
        public bool AllowLong { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Shorts", Order = 5, GroupName = "03. Entry")]
        public bool AllowShort { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Use declining volume", Order = 1, GroupName = "04. Filters")]
        public bool UseDecliningVolume { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Declining volume max ratio", Order = 2, GroupName = "04. Filters")]
        public double DecliningVolumeMax { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Use absorption", Order = 3, GroupName = "04. Filters")]
        public bool UseAbsorption { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Absorption min bars", Order = 4, GroupName = "04. Filters")]
        public int AbsorptionMinBars { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Absorption min delta", Order = 5, GroupName = "04. Filters")]
        public int AbsorptionMinDelta { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Use returning volume", Order = 6, GroupName = "04. Filters")]
        public bool UseReturningVolume { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Returning volume min ratio", Order = 7, GroupName = "04. Filters")]
        public double ReturningVolumeMin { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Stop buffer points", Order = 1, GroupName = "05. Risk")]
        public double StopBufferPoints { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Max stop points", Order = 2, GroupName = "05. Risk")]
        public double MaxStopPoints { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Min area points (0 = off)", Order = 3, GroupName = "05. Risk")]
        public double MinAreaPoints { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Print telemetry each session", Order = 1, GroupName = "06. Telemetry")]
        public bool PrintTelemetry { get; set; }
        #endregion

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Failed break of a rolling volume-profile value area, instrumented to measure its own filters.";
                Name = "AreaTrapStrategy";

                // OnEachTick, not OnPriceChange: the latter drops volume updates at
                // the same price, and volume at price is the entire subject here.
                Calculate = Calculate.OnEachTick;
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds = 30;
                IsFillLimitOnTouch = false;   // do not let a backtest hand us free limit fills
                BarsRequiredToTrade = 1;
                StartBehavior = StartBehavior.WaitUntilFlat;
                RealtimeErrorHandling = RealtimeErrorHandling.StopCancelClose;
                StopTargetHandling = StopTargetHandling.PerEntryExecution;

                WindowMinutes = 10;
                ArmMinutes = 10;
                Quantity = 1;
                ValueAreaPercent = 0.70;
                TicksPerLevel = 1;
                ExpandTwoRows = false;
                EntryOnBarClose = true;
                MinBreakPoints = 0.50;
                MaxBreakPoints = 0.0;
                AllowLong = true;
                AllowShort = true;
                UseDecliningVolume = false;   // OFF by default: they are the experiment,
                UseAbsorption = false;        // not a filter someone already validated.
                UseReturningVolume = false;
                DecliningVolumeMax = 0.80;
                AbsorptionMinBars = 2;
                AbsorptionMinDelta = 200;
                ReturningVolumeMin = 1.00;
                StopBufferPoints = 1.00;
                MaxStopPoints = 12.0;
                MinAreaPoints = 0.0;
                PrintTelemetry = true;
            }
            else if (State == State.Configure)
            {
                // The volumetric ladder is what makes the absorption filter possible
                // at all. Without it BuyVol/SellVol are zero and that filter simply
                // never passes, which is the honest failure rather than a fake one.
                AddVolumetric(Instrument.FullName, BarsPeriodType.Second, 30,
                              VolumetricDeltaType.BidAsk, TicksPerLevel);
                _volSeries = 1;
            }
            else if (State == State.DataLoaded)
            {
                _cfg = new AtConfig();
                _cfg.TickSize = TickSize;
                _cfg.TicksPerLevel = Math.Max(1, TicksPerLevel);
                _cfg.WindowMinutes = Math.Max(1, WindowMinutes);
                _cfg.ArmMinutes = Math.Max(1, ArmMinutes);
                _cfg.ValueAreaPercent = ValueAreaPercent;
                _cfg.ExpandTwoRows = ExpandTwoRows;
                _cfg.EntryMode = EntryOnBarClose ? AtEntryMode.BarClose : AtEntryMode.ReclaimTick;
                _cfg.UseDecliningVolume = UseDecliningVolume;
                _cfg.DecliningVolumeMax = DecliningVolumeMax;
                _cfg.UseAbsorption = UseAbsorption;
                _cfg.AbsorptionMinBars = AbsorptionMinBars;
                _cfg.AbsorptionMinDelta = AbsorptionMinDelta;
                _cfg.UseReturningVolume = UseReturningVolume;
                _cfg.ReturningVolumeMin = ReturningVolumeMin;
                _cfg.MinBreakPoints = MinBreakPoints;
                _cfg.MaxBreakPoints = MaxBreakPoints;
                _cfg.StopBufferPoints = StopBufferPoints;
                _cfg.MaxStopPoints = MaxStopPoints;
                _cfg.MinAreaPoints = MinAreaPoints;
                _cfg.AllowLong = AllowLong;
                _cfg.AllowShort = AllowShort;
                _engine = new AtEngine(_cfg);
            }
            else if (State == State.Terminated)
            {
                if (PrintTelemetry && _engine != null) DumpTelemetry("terminated");
            }
        }

        protected override void OnBarUpdate()
        {
            if (BarsInProgress != 0 || _engine == null) return;
            if (CurrentBar < 1) return;

            if (!_periodWarned && BarsPeriod.BarsPeriodType != BarsPeriodType.Second)
            {
                Print("AreaTrap: this chart is not a Second series. The window maths still runs off "
                      + "Time[0] and remains correct, but the design assumes 30-second bars.");
                _periodWarned = true;
            }

            // THE CORE SEES CLOSED BARS ONLY. Calculate is OnEachTick because the
            // profile has to redraw while the window develops, but OnBarUpdate then
            // fires on the FIRST tick of a forming bar, where open == high == low ==
            // close. Feeding that to the profile would spread a whole bar's volume
            // over a single price. IsFirstTickOfBar means the bar at index [1] has
            // just completed, and that is the one the engine gets.
            if (!IsFirstTickOfBar || CurrentBar < 2) return;

            DateTime bt = Time[1];

            // Trap 2: a Playback rewind re-feeds bars that were already consumed.
            // Gating on a strictly increasing timestamp is what keeps the profile
            // from counting the same volume twice; State.Realtime does not tell us.
            if (bt <= _lastBarTime) return;
            _lastBarTime = bt;

            // New session: nothing frozen carries across the close.
            if (_sessionDate == DateTime.MinValue || bt.Date != _sessionDate)
            {
                if (_sessionDate != DateTime.MinValue && PrintTelemetry)
                    DumpTelemetry("session " + _sessionDate.ToShortDateString());
                _sessionDate = bt.Date;
                _engine.SettleShadows(Close[1]);
                _engine.StartWindow(bt);
            }

            long buy = 0, sell = 0;
            ReadLadder(bt, out buy, out sell);

            AtBar bar = new AtBar(bt, Open[1], High[1], Low[1], Close[1],
                                  (long)Volume[1], buy, sell);
            AtDecision d = _engine.OnBar(bar);

            if (d.Action == AtAction.None) return;
            if (_entryInFlight || Position.MarketPosition != MarketPosition.Flat) return;

            Submit(d);
        }

        // Per-bar aggressor split from the volumetric series. Volumes[] is indexed
        // by an ABSOLUTE bar index (CurrentBars[n]) — a barsAgo offset here reads a
        // different bar and fails silently, which is the trap worth naming.
        private void ReadLadder(DateTime barTime, out long buy, out long sell)
        {
            buy = 0; sell = 0;
            if (_volSeries < 0 || BarsArray.Length <= _volSeries) return;
            VolumetricBarsType vb = BarsArray[_volSeries].BarsType as VolumetricBarsType;
            if (vb == null || vb.Volumes == null) return;

            // Resolved BY TIME, not by CurrentBars[n]. The two series roll on their
            // own schedules and the volumetric one may not have advanced yet when the
            // primary bar closes; taking the current index would then read a
            // different bar and be wrong without ever erroring.
            int idx = BarsArray[_volSeries].GetBar(barTime);
            if (idx < 0 || idx >= vb.Volumes.Length) return;
            buy = vb.Volumes[idx].TotalBuyingVolume;
            sell = vb.Volumes[idx].TotalSellingVolume;
        }

        private void Submit(AtDecision d)
        {
            bool isLong = d.Action == AtAction.EnterLong;
            string sig = isLong ? "AtLong" : "AtShort";

            // Brackets are attached before the entry, which is what NT8's managed
            // approach requires; doing it after leaves a naked position on a fill
            // that beats the next statement.
            SetStopLoss(sig, CalculationMode.Price, d.Stop, false);
            SetProfitTarget(sig, CalculationMode.Price, d.Target);

            _entryInFlight = true;   // raised BEFORE the call: order events arrive on their own thread

            if (_cfg.EntryMode == AtEntryMode.BarClose)
            {
                if (isLong) EnterLong(Quantity, sig); else EnterShort(Quantity, sig);
            }
            else if (GuardedLimit(isLong, d.Entry))
            {
                // A resting stop entry is the correct primitive for "buy the moment
                // price trades back above the level": a buy stop must sit ABOVE the
                // market, which is exactly where VAL + 1 tick is while we are below it.
                if (isLong) EnterLongStopMarket(Quantity, d.Entry, sig);
                else EnterShortStopMarket(Quantity, d.Entry, sig);
            }
            else
            {
                // The level is already on the wrong side of us: the reclaim happened
                // faster than a resting order could be placed. Taking it at market
                // would silently become a different trade at a worse price, so skip it
                // and let the telemetry keep the shadow. Backtests do not enforce this
                // and would have filled it, which is the whole reason for the check.
                _entryInFlight = false;
                Print(Time[0] + " AreaTrap: reclaim entry skipped, level already crossed ("
                      + d.Entry.ToString("F2") + " vs " + Close[0].ToString("F2") + ")");
            }
        }

        // The platform is not consistent about this so we are. See the header.
        private bool GuardedLimit(bool isLong, double price)
        {
            double last = Close[0];
            return isLong ? price > last : price < last;
        }

        protected override void OnPositionUpdate(Position position, double averagePrice,
                                                 int quantity, MarketPosition marketPosition)
        {
            if (marketPosition == MarketPosition.Flat)
            {
                _entryInFlight = false;
                if (_engine != null) _engine.OnTradeClosed(Time[0]);
            }
        }

        private void DumpTelemetry(string tag)
        {
            AtTelemetry t = _engine.Telemetry;
            Print("");
            Print("== AreaTrap telemetry (" + tag + ")  window=" + WindowMinutes + "m  arm=" + ArmMinutes + "m");
            Print("   windows " + t.Windows + "   reclaims " + t.Reclaims
                  + "   entries " + t.Entries + "   stale " + t.Expired);
            Print("   combination                          n     win%     avg pts       PF    worst");
            for (int m = 0; m < 8; m++)
            {
                AtCombo k = t.Combos[m];
                if (k.Trades == 0) continue;
                Print("   " + AtTelemetry.ComboName(m).PadRight(34)
                    + k.Trades.ToString().PadLeft(5)
                    + (k.WinRate * 100.0).ToString("F1").PadLeft(9)
                    + k.AvgPoints.ToString("F2").PadLeft(12)
                    + k.ProfitFactor.ToString("F3").PadLeft(9)
                    + k.WorstPoints.ToString("F2").PadLeft(9));
            }
            Print("   Read the rows, not the equity curve: row [0] is every reclaim with no filter,");
            Print("   so each other row is what that filter combination kept -- and what it threw away.");
        }
    }
}
