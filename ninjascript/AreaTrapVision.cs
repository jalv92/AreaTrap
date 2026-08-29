// AreaTrapVision.cs — the right ladder, look D of docs/mockups/chart-looks.html.
//
// A STANDALONE INDICATOR, not a companion to the strategy, and that is deliberate:
// drop it on a chart with no strategy running and you see the profile build, the
// value area freeze, and the state machine's phase in words. Debugging a strategy
// that draws nothing is the reason this file exists at all.
//
// It runs its OWN AtEngine over the same AreaTrapCore, so the picture and the
// decision can never disagree about what a value area is. The cost is that the
// profile is computed twice when both are loaded, which at 20-60 bars is nothing.
//
// ZOrder = -1 puts it behind the candles, which is what NinjaTrader's own shipped
// @VolumeProfile.cs does. The gutter the mockup reserved on the right cannot be
// taken from the price plot in NT8 -- the chart owns that layout -- so the ladder
// is anchored to the right edge and drawn behind price instead. Same reading, one
// less guarantee.
//
// ponytail: the histogram redraws on each CLOSED bar, not on each tick. On a 30s
// chart that is a visible update every 30 seconds while the window builds. Feeding
// the forming bar in as well would need a scratch copy of the profile per render;
// worth doing only if the 30-second step actually reads as stale.
#region Using declarations
using System;
using System.ComponentModel.DataAnnotations;
using System.Windows.Media;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.NinjaScript;
using SharpDX;
using SharpDX.Direct2D1;
using AreaTrapCore;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
    public class AreaTrapVision : Indicator
    {
        private AtEngine _engine;
        private AtConfig _cfg;
        private DateTime _lastBarTime = DateTime.MinValue;
        private DateTime _sessionDate = DateTime.MinValue;

        private SolidColorBrush _bProfile, _bProfileOut, _bBand, _bLevel, _bPoc, _bText, _bPanel, _bPanelEdge;
        private StrokeStyle _dashed;
        private SharpDX.DirectWrite.TextFormat _font, _fontSmall;

        #region Parameters
        [NinjaScriptProperty]
        [Display(Name = "Window minutes", Order = 1, GroupName = "01. Cycle")]
        public int WindowMinutes { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Arm minutes (stale timeout)", Order = 2, GroupName = "01. Cycle")]
        public int ArmMinutes { get; set; }

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
        [Display(Name = "Ladder width (% of panel)", Order = 1, GroupName = "03. Look")]
        public int LadderWidthPercent { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Show status block", Order = 2, GroupName = "03. Look")]
        public bool ShowStatus { get; set; }
        #endregion

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Rolling volume profile pinned to the right edge, with its value area and the AreaTrap state machine in words.";
                Name = "AreaTrapVision";
                Calculate = Calculate.OnEachTick;
                IsOverlay = true;
                DrawOnPricePanel = true;
                IsSuspendedWhileInactive = false;
                PaintPriceMarkers = false;
                ZOrder = -1;                 // behind the candles, as NT8's own VolumeProfile does

                WindowMinutes = 10;
                ArmMinutes = 10;
                ValueAreaPercent = 0.70;
                TicksPerLevel = 1;
                ExpandTwoRows = false;
                LadderWidthPercent = 16;
                ShowStatus = true;
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
                _engine = new AtEngine(_cfg);
            }
            else if (State == State.Terminated)
            {
                DisposeBrushes();
            }
        }

        protected override void OnBarUpdate()
        {
            if (_engine == null || CurrentBar < 2) return;
            if (!IsFirstTickOfBar) return;           // closed bars only; see AreaTrapStrategy

            DateTime bt = Time[1];
            if (bt <= _lastBarTime) return;          // a Playback rewind re-feeds bars
            _lastBarTime = bt;

            if (_sessionDate == DateTime.MinValue || bt.Date != _sessionDate)
            {
                _sessionDate = bt.Date;
                _engine.StartWindow(bt);
            }

            _engine.OnBar(new AtBar(bt, Open[1], High[1], Low[1], Close[1], (long)Volume[1], 0, 0));
        }

        public override void OnRenderTargetChanged()
        {
            DisposeBrushes();
            if (RenderTarget == null) return;

            _bProfile    = new SolidColorBrush(RenderTarget, new Color4(0.878f, 0.651f, 0.235f, 0.72f));
            _bProfileOut = new SolidColorBrush(RenderTarget, new Color4(0.878f, 0.651f, 0.235f, 0.32f));
            _bBand       = new SolidColorBrush(RenderTarget, new Color4(0.878f, 0.651f, 0.235f, 0.10f));
            _bLevel      = new SolidColorBrush(RenderTarget, new Color4(0.878f, 0.651f, 0.235f, 0.95f));
            _bPoc        = new SolidColorBrush(RenderTarget, new Color4(0.949f, 0.784f, 0.408f, 0.95f));
            _bText       = new SolidColorBrush(RenderTarget, new Color4(0.788f, 0.827f, 0.863f, 1f));
            _bPanel      = new SolidColorBrush(RenderTarget, new Color4(0.043f, 0.059f, 0.078f, 0.82f));
            _bPanelEdge  = new SolidColorBrush(RenderTarget, new Color4(0.333f, 0.388f, 0.435f, 0.60f));

            StrokeStyleProperties ssp = new StrokeStyleProperties();
            ssp.DashStyle = SharpDX.Direct2D1.DashStyle.Dash;
            _dashed = new StrokeStyle(RenderTarget.Factory, ssp);

            _font = new SharpDX.DirectWrite.TextFormat(NinjaTrader.Core.Globals.DirectWriteFactory, "Consolas", 11f);
            _fontSmall = new SharpDX.DirectWrite.TextFormat(NinjaTrader.Core.Globals.DirectWriteFactory, "Consolas", 10f);
        }

        private void DisposeBrushes()
        {
            if (_bProfile != null)    { _bProfile.Dispose();    _bProfile = null; }
            if (_bProfileOut != null) { _bProfileOut.Dispose(); _bProfileOut = null; }
            if (_bBand != null)       { _bBand.Dispose();       _bBand = null; }
            if (_bLevel != null)      { _bLevel.Dispose();      _bLevel = null; }
            if (_bPoc != null)        { _bPoc.Dispose();        _bPoc = null; }
            if (_bText != null)       { _bText.Dispose();       _bText = null; }
            if (_bPanel != null)      { _bPanel.Dispose();      _bPanel = null; }
            if (_bPanelEdge != null)  { _bPanelEdge.Dispose();  _bPanelEdge = null; }
            if (_dashed != null)      { _dashed.Dispose();      _dashed = null; }
            if (_font != null)        { _font.Dispose();        _font = null; }
            if (_fontSmall != null)   { _fontSmall.Dispose();   _fontSmall = null; }
        }

        protected override void OnRender(ChartControl chartControl, ChartScale chartScale)
        {
            if (_engine == null || RenderTarget == null || _bProfile == null) return;

            float panelX = ChartPanel.X, panelW = ChartPanel.W;
            float right = panelX + panelW;
            float ladderW = Math.Max(40f, panelW * (Math.Max(4, Math.Min(40, LadderWidthPercent)) / 100f));

            AtProfile p = _engine.Profile;
            long peak = p.PeakVolume();

            // ---- the value area, drawn first so everything sits on top of it
            AtValueArea va = _engine.Area;
            bool frozen = va.Valid;
            if (frozen)
            {
                float yTop = chartScale.GetYByValue(va.Vah);
                float yBot = chartScale.GetYByValue(va.Val);
                RenderTarget.FillRectangle(new RectangleF(panelX, yTop, panelW, Math.Max(1f, yBot - yTop)), _bBand);

                DrawLevel(chartScale, panelX, right, va.Vah, _bLevel, null, "VAH " + va.Vah.ToString("F2"));
                DrawLevel(chartScale, panelX, right, va.Val, _bLevel, null, "VAL " + va.Val.ToString("F2"));
                DrawLevel(chartScale, panelX, right, va.Poc, _bPoc, _dashed, "POC " + va.Poc.ToString("F2"));
            }

            // ---- the ladder, anchored to the right edge
            if (peak > 0)
            {
                double rowSize = p.RowSize;
                System.Collections.Generic.List<long> rows = p.SortedRows();
                for (int i = 0; i < rows.Count; i++)
                {
                    double price = p.PriceOf(rows[i]);
                    long v = p.VolumeAt(price);
                    if (v <= 0) continue;

                    float yA = chartScale.GetYByValue(price + rowSize);
                    float yB = chartScale.GetYByValue(price);
                    float h = Math.Max(1f, yB - yA);
                    float w = (float)(ladderW * ((double)v / peak));
                    bool inside = frozen && price >= va.Val && price <= va.Vah;
                    RenderTarget.FillRectangle(new RectangleF(right - w, yA, w, Math.Max(1f, h - 0.5f)),
                                               inside ? _bProfile : _bProfileOut);
                }
            }

            if (ShowStatus) DrawStatus(panelX, ChartPanel.Y);
        }

        private void DrawLevel(ChartScale scale, float x0, float x1, double price,
                               SolidColorBrush brush, StrokeStyle style, string label)
        {
            float y = scale.GetYByValue(price);
            if (style == null) RenderTarget.DrawLine(new Vector2(x0, y), new Vector2(x1, y), brush, 1f);
            else RenderTarget.DrawLine(new Vector2(x0, y), new Vector2(x1, y), brush, 1f, style);
            RenderTarget.DrawText(label, _fontSmall, new RectangleF(x0 + 6f, y - 14f, 220f, 14f), brush);
        }

        private void DrawStatus(float x, float y)
        {
            string phase = _engine.Phase == AtPhase.Building ? "BUILDING"
                         : _engine.Phase == AtPhase.Armed ? "ARMED" : "IN TRADE";
            string l2 = _engine.Area.Valid
                ? "AREA  " + _engine.Area.Width.ToString("F2") + " pts   cov "
                    + (_engine.Area.Coverage * 100.0).ToString("F0") + "%"
                : "AREA  --";
            string l3 = _engine.Phase == AtPhase.Building
                ? "WINDOW " + WindowMinutes + "m building"
                : (_engine.HasBreak ? "BREAK at " + _engine.BreakExtreme.ToString("F2")
                                    : "HUNT   break + reclaim");
            string l4 = "reclaims " + _engine.Telemetry.Reclaims + "   windows " + _engine.Telemetry.Windows;

            float bx = x + 10f, by = y + 10f, bw = 210f, bh = 76f;
            RenderTarget.FillRectangle(new RectangleF(bx, by, bw, bh), _bPanel);
            RenderTarget.DrawRectangle(new RectangleF(bx, by, bw, bh), _bPanelEdge, 1f);
            RenderTarget.DrawText(phase, _font, new RectangleF(bx + 9f, by + 7f, bw - 12f, 16f), _bLevel);
            RenderTarget.DrawText(l2, _fontSmall, new RectangleF(bx + 9f, by + 25f, bw - 12f, 14f), _bText);
            RenderTarget.DrawText(l3, _fontSmall, new RectangleF(bx + 9f, by + 41f, bw - 12f, 14f), _bText);
            RenderTarget.DrawText(l4, _fontSmall, new RectangleF(bx + 9f, by + 57f, bw - 12f, 14f), _bText);
        }
    }
}
