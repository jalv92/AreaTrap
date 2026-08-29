// Test harness for AreaTrapCore.cs. No framework: an assert counter and a Main
// that exits non-zero, which is all a build gate needs and all anyone has to
// learn. Running it is ALSO the compile gate for the core, because `nt8c check`
// on one file cannot see a sibling's types.
//
// Run: dotnet run --project tests
using System;
using System.Collections.Generic;
using AreaTrapCore;

public static class T
{
    public static int Failures, Checks;

    public static void Check(bool ok, string name)
    {
        Checks++;
        if (ok) { Console.WriteLine("  PASS " + name); return; }
        Failures++;
        Console.WriteLine("  FAIL " + name);
    }

    public static void CheckClose(double a, double b, string name, double eps = 1e-9)
    {
        Check(Math.Abs(a - b) <= eps, name + " (" + a.ToString("R") + " vs " + b.ToString("R") + ")");
    }

    public static void CheckInt(long a, long b, string name)
    {
        Check(a == b, name + " (" + a + " vs " + b + ")");
    }

    public static void Section(string name)
    {
        Console.WriteLine();
        Console.WriteLine("== " + name);
    }
}

public static class Program
{
    private static AtConfig Cfg()
    {
        AtConfig c = new AtConfig();
        c.TickSize = 0.25;
        c.TicksPerLevel = 2;              // 0.5-point rows, matching the fixture
        c.WindowMinutes = 20;             // 40 bars of 30 seconds
        c.ArmMinutes = 20;
        c.ExpandTwoRows = true;           // the fixture's headline numbers
        c.UseDecliningVolume = false;
        c.UseAbsorption = false;
        c.UseReturningVolume = false;
        return c;
    }

    public static int Main()
    {
        Console.WriteLine("AreaTrapCore tests");

        // ---------------------------------------------------------- profile
        T.Section("profile");
        {
            AtProfile p = new AtProfile();
            p.TickSize = 0.25; p.TicksPerLevel = 2;
            p.Add(20000.00, 100, 60, 40);
            p.Add(20000.30, 50, 20, 30);      // same 0.5 row as 20000.00
            p.Add(20001.00, 10, 5, 5);
            T.CheckInt(p.TotalVolume, 160, "total volume conserved");
            T.CheckInt(p.RowCount, 2, "prices in the same row collapse");
            T.CheckInt(p.VolumeAt(20000.00), 150, "row accumulates");
            T.CheckInt(p.AskVolumeAt(20000.00), 80, "ask side accumulates");
            T.CheckInt(p.BidVolumeAt(20000.00), 70, "bid side accumulates");
            T.CheckInt(p.VolumeAt(19995.00), 0, "untraded price reads zero and is not a row");

            AtProfile q = new AtProfile();
            q.TickSize = 0.25; q.TicksPerLevel = 2;
            q.AddBarSpread(new AtBar(DateTime.Now, 100, 102, 100, 101, 7, 4, 3));
            T.CheckInt(q.TotalVolume, 7, "bar spread conserves volume exactly (7 over 5 rows)");
        }

        // ------------------------------------------------------- value area
        T.Section("value area vs the Python reference");
        {
            AtProfile p = new AtProfile();
            p.TickSize = 0.25; p.TicksPerLevel = 2;
            List<AtBar> build = CycleFixture.Bars(true);
            T.CheckInt(build.Count, CycleFixture.BuildBarCount, "fixture build bar count");
            foreach (AtBar b in build) p.AddBarSpread(b);
            T.CheckInt(p.TotalVolume, CycleFixture.BuildTotalVolume, "profile total matches reference");

            AtValueArea two = AtValueAreaMath.Compute(p, 0.70, true);
            T.CheckClose(two.Poc, CycleFixture.TwoRowPoc, "2-vs-2 POC");
            T.CheckClose(two.Val, CycleFixture.TwoRowVal, "2-vs-2 VAL");
            T.CheckClose(two.Vah, CycleFixture.TwoRowVah, "2-vs-2 VAH");
            T.CheckClose(two.Coverage, CycleFixture.TwoRowCoverage, "2-vs-2 coverage", 1e-6);

            AtValueArea one = AtValueAreaMath.Compute(p, 0.70, false);
            T.CheckClose(one.Poc, CycleFixture.OneRowPoc, "1-vs-1 POC");
            T.CheckClose(one.Val, CycleFixture.OneRowVal, "1-vs-1 VAL");
            T.CheckClose(one.Vah, CycleFixture.OneRowVah, "1-vs-1 VAH");

            // The whole reason ExpandTwoRows exists: the two conventions do not
            // agree, so picking one silently would be picking a number silently.
            T.Check(one.Val != two.Val || one.Vah != two.Vah,
                    "the two expansion rules genuinely disagree");
            T.Check(two.Coverage >= 0.70 && one.Coverage >= 0.70, "both cover at least 70%");
            T.Check(two.Coverage < 0.80 && one.Coverage < 0.80, "neither overshoots past 80%");
            T.Check(two.Val < two.Poc && two.Poc < two.Vah, "POC sits inside the area");
        }

        T.Section("value area edge cases");
        {
            AtProfile empty = new AtProfile();
            T.Check(!AtValueAreaMath.Compute(empty, 0.70, false).Valid, "empty profile yields no area");

            AtProfile one = new AtProfile();
            one.TickSize = 1.0; one.TicksPerLevel = 1;
            one.Add(50, 10, 0, 0);
            AtValueArea va = AtValueAreaMath.Compute(one, 0.70, false);
            T.Check(va.Valid && va.Val == va.Vah && va.Val == 50, "single row is its own area");

            // A gappy profile: the sparse model must not spend the expansion budget
            // walking over prices that never traded.
            AtProfile gap = new AtProfile();
            gap.TickSize = 1.0; gap.TicksPerLevel = 1;
            gap.Add(0, 10, 0, 0); gap.Add(2, 50, 0, 0); gap.Add(3, 5, 0, 0); gap.Add(4, 40, 0, 0);
            AtValueArea g = AtValueAreaMath.Compute(gap, 0.70, false);
            T.CheckClose(g.Poc, 2, "gappy POC");
            T.Check(g.Coverage >= 0.70, "gappy area still covers 70%");
        }

        // -------------------------------------------------------- telemetry
        T.Section("telemetry combination scoring");
        {
            AtTelemetry t = new AtTelemetry();
            // declining passed, absorption failed, returning passed -> mask 0b101 = 5
            int verdict = AtTelemetry.MaskOf(true, false, true);
            T.CheckInt(verdict, 5, "verdict mask packs the three filters");
            t.Record(verdict, 3.0);
            T.CheckInt(t.Combos[0].Trades, 1, "no-filter combo takes every event");
            T.CheckInt(t.Combos[1].Trades, 1, "declining-only accepts it");
            T.CheckInt(t.Combos[4].Trades, 1, "returning-only accepts it");
            T.CheckInt(t.Combos[5].Trades, 1, "declining+returning accepts it");
            T.CheckInt(t.Combos[2].Trades, 0, "absorption-only rejects it");
            T.CheckInt(t.Combos[3].Trades, 0, "declining+absorption rejects it");
            T.CheckInt(t.Combos[7].Trades, 0, "all-three rejects it");

            t.Record(7, -1.0);   // everything passed, a loser
            T.CheckInt(t.Combos[7].Trades, 1, "all-three now has its event");
            T.CheckInt(t.Combos[0].Trades, 2, "control arm has both");
            T.CheckClose(t.Combos[0].AvgPoints, 1.0, "control arm average");
            T.CheckClose(t.Combos[0].WinRate, 0.5, "control arm win rate");
            T.CheckClose(t.Combos[0].ProfitFactor, 3.0, "control arm profit factor");
            T.CheckClose(t.Combos[0].WorstPoints, 1.0, "control arm worst loss");
        }

        // ------------------------------------------------------ the machine
        T.Section("state machine");
        {
            AtEngine e = new AtEngine(Cfg());
            List<AtBar> all = CycleFixture.Bars(false);

            T.Check(e.Phase == AtPhase.Building, "starts building");
            for (int i = 0; i < 40; i++) e.OnBar(all[i]);
            T.Check(e.Phase == AtPhase.Building, "still building at 19:30 of a 20-minute window");
            e.OnBar(all[40]);                 // t0 + 20:00 exactly: the boundary bar
            T.Check(e.Phase == AtPhase.Armed, "arms on the bar that reaches the boundary");
            T.CheckInt(e.Profile.TotalVolume, CycleFixture.BuildTotalVolume,
                       "the boundary bar is NOT in the profile (no one-bar lookahead)");
            T.CheckClose(e.Area.Val, CycleFixture.TwoRowVal, "frozen VAL");
            T.CheckClose(e.Area.Vah, CycleFixture.TwoRowVah, "frozen VAH");

            AtDecision entry = new AtDecision();
            int entryBar = -1;
            for (int i = 41; i < all.Count; i++)
            {
                AtDecision d = e.OnBar(all[i]);
                if (d.Action != AtAction.None) { entry = d; entryBar = i; break; }
            }
            T.Check(entryBar > 0, "the cycle produces an entry");
            T.Check(entry.Action == AtAction.EnterLong, "and it is the long side");
            T.Check(entry.Entry > e.Area.Val, "entry is back inside the area");
            T.Check(entry.Target == e.Area.Vah, "target is the far side");
            T.Check(entry.Stop < e.Area.Val, "stop is below the area");
            T.Check(e.Phase == AtPhase.InTrade, "phase advances to in-trade");

            e.OnTradeClosed(all[all.Count - 1].Time);
            T.Check(e.Phase == AtPhase.Building, "a closed trade restarts the cycle");
            T.CheckInt(e.Profile.TotalVolume, 0, "and the new window starts empty");
        }

        T.Section("stale area expires");
        {
            AtConfig c = Cfg();
            c.ArmMinutes = 1;                 // two bars
            AtEngine e = new AtEngine(c);
            List<AtBar> all = CycleFixture.Bars(false);
            for (int i = 0; i <= 40; i++) e.OnBar(all[i]);
            T.Check(e.Phase == AtPhase.Armed, "armed before the timeout");
            for (int i = 41; i < 45; i++) e.OnBar(all[i]);
            T.Check(e.Telemetry.Expired >= 1, "the stale area is retired");
            T.Check(e.Phase == AtPhase.Building, "and a new window opens");
        }

        T.Section("stop cap");
        {
            AtConfig c = Cfg();
            c.MaxStopPoints = 4.0;
            AtEngine e = new AtEngine(c);
            List<AtBar> all = CycleFixture.Bars(false);
            for (int i = 0; i < 40; i++) e.OnBar(all[i]);
            AtDecision entry = new AtDecision();
            for (int i = 40; i < all.Count; i++)
            {
                AtDecision d = e.OnBar(all[i]);
                if (d.Action != AtAction.None) { entry = d; break; }
            }
            T.Check(entry.Action == AtAction.EnterLong, "still enters with a capped stop");
            T.CheckClose(entry.Entry - entry.Stop, 4.0,
                         "risk is exactly the cap, not the distance to the break low");
        }

        T.Section("a filtered setup is still measured");
        {
            AtConfig c = Cfg();
            c.UseAbsorption = true;
            c.AbsorptionMinDelta = long.MaxValue / 4;   // impossible: nothing can pass
            AtEngine e = new AtEngine(c);
            List<AtBar> all = CycleFixture.Bars(false);
            bool tookOne = false;
            foreach (AtBar b in all) if (e.OnBar(b).Action != AtAction.None) tookOne = true;
            T.Check(!tookOne, "an impossible filter takes no trades");
            T.Check(e.Telemetry.Reclaims >= 1, "but the reclaim was still seen");
            e.SettleShadows(all[all.Count - 1].Close);
            T.Check(e.Telemetry.Combos[0].Trades >= 1,
                    "and the control arm recorded what the filter rejected");
            T.CheckInt(e.Telemetry.Combos[2].Trades, 0, "while the absorption arm stayed empty");
        }

        T.Section("end to end, telemetry as it will print in Playback");
        {
            AtConfig c = Cfg();
            c.UseDecliningVolume = false; c.UseAbsorption = false; c.UseReturningVolume = false;
            AtEngine e = new AtEngine(c);
            List<AtBar> all = CycleFixture.Bars(false);
            foreach (AtBar b in all) e.OnBar(b);
            e.SettleShadows(all[all.Count - 1].Close);
            Console.WriteLine("   windows " + e.Telemetry.Windows + "  reclaims " + e.Telemetry.Reclaims
                              + "  entries " + e.Telemetry.Entries + "  expired " + e.Telemetry.Expired);
            for (int m = 0; m < 8; m++)
            {
                AtCombo k = e.Telemetry.Combos[m];
                if (k.Trades == 0) continue;
                Console.WriteLine("   [" + m + "] " + AtTelemetry.ComboName(m).PadRight(34)
                                  + " n=" + k.Trades + "  avg=" + k.AvgPoints.ToString("F2") + " pts");
            }
            T.Check(e.Telemetry.Windows >= 1, "at least one window was built");
            T.Check(e.Telemetry.Combos[0].Trades >= 1, "the control arm has data");
        }

        Console.WriteLine();
        Console.WriteLine(T.Failures == 0
            ? "ALL " + T.Checks + " CHECKS PASSED"
            : T.Failures + " of " + T.Checks + " CHECKS FAILED");
        return T.Failures == 0 ? 0 : 1;   // 0 = success, so a CI gate can trust it
    }
}
