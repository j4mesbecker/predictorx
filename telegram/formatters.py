"""
PredictorX — Telegram Message Formatters
Compact, color-coded alerts.

Platform indicators:
  🔵 = ThinkorSwim (Schwab)
  🟢 = Kalshi
"""

from core.models import Prediction, Opportunity, VixSnapshot

# Platform color dots
TOS = "\U0001f535"   # blue circle — ThinkorSwim
KAL = "\U0001f7e2"   # green circle — Kalshi


def format_opportunity(opp: Opportunity) -> str:
    """Compact single opportunity — one block per trade idea."""
    p = opp.prediction
    edge = f"+{p.edge:.1%}" if p.edge > 0 else f"{p.edge:.1%}"
    conf = f"{p.confidence_score:.0%}"

    parts = []

    # Kalshi line
    if p.recommended_contracts > 0:
        parts.append(
            f"{KAL} <b>{p.market_title}</b>\n"
            f"   {p.side.upper()} {p.recommended_contracts}x @ ${p.recommended_cost:.2f} "
            f"| {edge} edge | {conf} conf"
        )

    # ToS line (primary suggestion only)
    tos_trades = p.confidence_factors.get("tos_trades", [])
    if tos_trades:
        t = tos_trades[0]
        risk = f" | ${t['max_risk']} risk" if t.get("max_risk") else ""
        parts.append(f"{TOS} {t['description']}{risk}")

    if not parts:
        parts.append(f"{KAL} {p.market_title} | {edge} edge | {conf} conf")

    return "\n".join(parts)


def format_morning_scan(opportunities: list[Opportunity], vix: VixSnapshot = None) -> str:
    """Compact morning scan."""
    header = "<b>MORNING SCAN</b>"
    if vix:
        header += f"  |  VIX {vix.price:.1f} ({vix.regime})"
    lines = [header, ""]

    if not opportunities:
        lines.append("No opportunities.")
        return "\n".join(lines)

    for opp in opportunities[:5]:
        lines.append(format_opportunity(opp))
        lines.append("")

    total = sum(o.prediction.recommended_cost for o in opportunities[:5])
    lines.append(f"Deploy: <b>${total:.2f}</b>")
    return "\n".join(lines)


def format_tail_analysis(predictions: list[Prediction], vix: VixSnapshot = None) -> str:
    """Compact tail analysis with separate Kalshi + ToS lines."""
    header = "<b>S&P TAILS</b>"
    if vix:
        header += f"  |  VIX {vix.price:.1f} ({vix.regime})"
        if vix.spx_price:
            header += f"  |  SPX {vix.spx_price:,.0f}"
    lines = [header, ""]

    if vix:
        regime_short = {
            "LOW": "0% hist loss on >2% drops",
            "LOW_MED": "Sell >3% and >5% only",
            "MEDIUM": "Only >5%, thin edge",
            "HIGH": "No tails",
            "CRISIS": "ALL CASH",
        }
        lines.append(regime_short.get(vix.regime, ""))
        lines.append("")

    if not predictions:
        lines.append("No tail opportunities.")
        return "\n".join(lines)

    # Kalshi tails
    for p in predictions:
        pct = p.confidence_factors.get("pct_drop", "?")
        hist = p.confidence_factors.get("hist_prob", 0)
        if p.recommended_contracts > 0:
            lines.append(
                f"{KAL} <b>>{pct}%</b> drop — "
                f"{p.recommended_contracts}x @ ${p.recommended_cost:.2f} "
                f"| +{p.edge:.1%} edge | {hist:.2%} hist"
            )
        else:
            lines.append(
                f"{KAL} <b>>{pct}%</b> drop — +{p.edge:.1%} edge | {hist:.2%} hist"
            )

    # ToS equivalents (from first prediction)
    if predictions:
        tos_trades = predictions[0].confidence_factors.get("tos_trades", [])
        for t in tos_trades:
            risk = f" | ${t['max_risk']} risk" if t.get("max_risk") else ""
            note = f"\n   {t['note']}" if t.get("note") else ""
            lines.append(f"{TOS} {t['description']}{risk}{note}")

    return "\n".join(lines)


def format_weather_predictions(predictions: list[Prediction]) -> str:
    """Compact weather predictions — all Kalshi."""
    lines = [f"<b>WEATHER</b>", ""]

    if not predictions:
        lines.append("No weather opportunities.")
        return "\n".join(lines)

    for p in predictions:
        city = p.confidence_factors.get("city", "?")
        edge = f"+{p.edge:.1%}"
        conf = f"{p.confidence_score:.0%}"
        if p.recommended_contracts > 0:
            lines.append(
                f"{KAL} <b>{city}</b> — {p.recommended_contracts}x @ ${p.recommended_cost:.2f} "
                f"| {edge} | {conf}"
            )
        else:
            lines.append(f"{KAL} <b>{city}</b> — {edge} | {conf}")

    return "\n".join(lines)


def format_performance_summary(perf: dict) -> str:
    """Compact performance."""
    total = perf.get("total_predictions", 0)
    accuracy = perf.get("accuracy", 0)
    pnl = perf.get("total_pnl", 0)

    lines = [
        f"<b>PERFORMANCE</b>  |  {total} trades | {accuracy:.0%} acc | ${pnl:+.2f}",
    ]

    by_strategy = perf.get("by_strategy", {})
    if by_strategy:
        for name, s in by_strategy.items():
            lines.append(
                f"  {name.upper()}: {s.get('count',0)}x "
                f"{s.get('accuracy',0):.0%} ${s.get('pnl',0):+.2f}"
            )

    return "\n".join(lines)


def format_status(data: dict) -> str:
    """Compact system status."""
    lines = [
        f"<b>STATUS</b>  |  Last: {data.get('last_scan', 'Never')} "
        f"| Active: {data.get('active_predictions', 0)}",
    ]

    adapters = data.get("adapters", {})
    if adapters:
        up = [n for n, ok in adapters.items() if ok]
        down = [n for n, ok in adapters.items() if not ok]
        if down:
            lines.append(f"\u274c Down: {', '.join(down)}")
        if up:
            lines.append(f"\u2705 {', '.join(up)}")

    return "\n".join(lines)


def format_spx_drop_alert(alert: dict) -> str:
    """
    Reactive SPX drop alert — backed by 6,563-day backtest.
    Two modes:
      -1% / -1.5% → DIP BUY alert with call options to buy
      -2% / -3% / -5% → Tail trade alert with premium selling + bounce trades
    """
    d = alert
    lines = []
    drop = d["drop_pct"]
    spy_price = d["spx_price"] / 10

    # ── Header: what just happened ───────────────────────────
    lines.append(
        f"<b>SPX {d['change_pct']:+.1f}%</b>  ${d['spx_price']:,.0f}"
        f"  |  SPY ~${spy_price:.0f}"
        f"  |  VIX {d['vix_price']:.1f} ({d['regime']})"
    )
    lines.append("")

    # ── Blocked? Show why and stop ───────────────────────────
    if d["blocked"]:
        for reason in d["block_reasons"]:
            lines.append(f"\u274c {reason}")
        lines.append("DO NOT TRADE")
        return "\n".join(lines)

    # ── DIP BUY mode (-1% and -1.5%) ────────────────────────
    if drop <= 1.5:
        bounce_pct = 98.0 if d["regime"] in ("LOW", "LOW_MED") else 95.0
        lines.append(
            f"<b>DIP BUY SIGNAL</b> — {bounce_pct:.0f}% bounce rate"
            f" in {d['regime']} VIX"
        )
        lines.append(
            f"{d['sample_days']:,}-day backtest: {d['regime']} dips >{drop:.0f}%"
            f" recover within 1-5 days"
        )

        if d["cluster_warning"]:
            lines.append("\u26a0\ufe0f Back-to-back red day — BLOCKED, wait for VIX reversion")

        lines.append("")
        lines.append("<b>BUY CALLS (at dip price, 14+ DTE):</b>")

        call_options = d.get("call_options", [])
        for c in call_options:
            lines.append(f"{TOS} {c['ticker']} {c['strike']} ({c['label']})")
            lines.append(f"   {c['note']}")

        if not call_options:
            atm_spy = round(spy_price)
            lines.append(f"{TOS} SPY {atm_spy}C — ATM at dip, 14+ DTE")
            lines.append(f"{TOS} SPY {atm_spy + 3}C — slightly OTM, cheaper")

        lines.append("")
        target_spx = d["spx_open"] * (1 - 0.005)  # -0.5% from open
        target_spy = target_spx / 10
        lines.append(f"Target: SPY ~${target_spy:.0f} (half the drop recovered)")
        lines.append(f"Entry window: 8:30-9:30 AM CST (options open, let IV settle)")
        lines.append(f"NO WEEKLIES — multi-day selloffs kill them")

        # Append naked put signal if available
        naked_put = d.get("naked_put")
        if naked_put and not naked_put.get("blocked"):
            lines.append("")
            lines.append("\u2500" * 30)
            lines.append("")
            lines.append(format_naked_put_signal(naked_put))

        return "\n".join(lines)

    # ── TAIL TRADE mode (-2%, -3%, -5%) ──────────────────────
    if d["hist_prob"] == 0:
        lines.append(
            f"<b>{d['win_rate']:.0%} win rate</b> selling >{drop:.0f}% tails"
            f" in {d['regime']} VIX"
        )
        lines.append(
            f"0 losses in {d['sample_days']:,} days (25yr backtest)"
        )
    else:
        lines.append(
            f"<b>{d['win_rate']:.1%} win rate</b> selling >{drop:.0f}% tails"
            f" in {d['regime']} VIX"
        )
        lines.append(
            f"Hist loss rate: {d['hist_prob']:.2%}"
            f" ({d['sample_days']:,} day sample)"
        )

    lines.append(f"Edge: <b>{d['rating']}</b> — mkt overprices ~{d['est_market_price']:.0%} vs {d['hist_prob']:.2%} fair")

    if d["cluster_warning"]:
        lines.append("\u26a0\ufe0f Clustering: yesterday was a big drop — elevated risk")

    lines.append("")

    # Bounce trade calls
    lines.append("<b>BOUNCE TRADE:</b>")
    call_options = d.get("call_options", [])
    if call_options:
        for c in call_options:
            lines.append(f"{TOS} BUY {c['ticker']} {c['strike']} ({c['label']})")
    else:
        atm_spy = round(spy_price)
        lines.append(f"{TOS} BUY SPY {atm_spy}C — ATM bounce play")

    lines.append("")
    lines.append("<b>TAIL PREMIUM:</b>")

    # Kalshi
    lines.append(f"{KAL} SELL YES on >{drop:.0f}% drop — collect premium")

    # ToS
    for t in d["tos_trades"]:
        if t["instrument"] == "SPY":
            lines.append(f"{TOS} {t['action']} SPY | ${t['risk']} risk")
        else:
            margin = f" | ~${t['margin']} margin" if t.get("margin") else ""
            lines.append(f"{TOS} {t['action']} {t['instrument']}{margin}")

    # Append naked put signal if available
    naked_put = d.get("naked_put")
    if naked_put and not naked_put.get("blocked"):
        lines.append("")
        lines.append("\u2500" * 30)
        lines.append("")
        lines.append(format_naked_put_signal(naked_put))

    return "\n".join(lines)


def format_stock_level_alert(alert: dict) -> str:
    """
    Format stock level alert (TSLA, NVDA, etc).
    Handles both level-hit and proximity alerts.
    """
    d = alert
    ticker = d["ticker"]
    price = d["price"]
    change_pct = d["change_pct"]
    lines = []

    if d["alert_type"] == "stock_proximity":
        # ── Proximity warning ──────────────────────────────
        lines.append(f"\U0001f4ca <b>LEVEL ALERT</b> [BRANDO]")
        lines.append("")
        direction = "\u2191" if d["direction"] == "above" else "\u2193"
        lines.append(
            f"<b>{ticker} ${price:.2f}</b> ({change_pct:+.1f}%)"
            f"  |  {direction} {d['distance_pct']:.1f}% from {d['level_label']}"
        )
        lines.append("")
        lines.append(
            f"Approaching <b>${d['level_price']:.0f}</b> — {d['level_label']}"
        )
        lines.append(f"{d['action']}")
        lines.append("")
        lines.append(f"<b>Trade plan:</b> {d['trade']}")
        lines.append("")

        # Append options signal if available
        opt_sig = d.get("options_signal")
        if opt_sig and not opt_sig.get("blocked"):
            lines.append("\u2500" * 30)
            lines.append("")
            if opt_sig.get("action") == "SELL_PUT":
                lines.append(format_naked_put_signal(opt_sig))
            elif opt_sig.get("action") == "SELL_CALL":
                lines.append(format_naked_call_signal(opt_sig))
            lines.append("")

        lines.append(f"Options window: 8:30 AM - 3:30 PM CST")
        return "\n".join(lines)

    # ── Level hit ──────────────────────────────────────────
    lines.append(f"\U0001f4ca <b>LEVEL ALERT</b> [BRANDO]")
    lines.append("")
    lines.append(
        f"<b>{ticker} ${price:.2f}</b> ({change_pct:+.1f}%)"
        f"  |  <b>{d['level_label']} ${d['level_price']:.0f}</b>"
    )
    lines.append(
        f"Session: ${d['session_high']:.2f}H / ${d['session_low']:.2f}L"
    )
    lines.append("")
    lines.append(f"<b>{d['action']}</b>")
    lines.append("")

    # Trade action
    lines.append(f"{TOS} <b>{d['trade']}</b>")
    lines.append("")

    # Level map (show all levels for this ticker)
    all_levels = d.get("all_levels", {})
    if all_levels:
        lines.append("<b>Level Map:</b>")
        sorted_lvls = sorted(all_levels.values(), key=lambda x: x["price"], reverse=True)
        for lvl in sorted_lvls:
            marker = " \u25c0 YOU ARE HERE" if abs(lvl["price"] - price) / price < 0.01 else ""
            hit = " \u2705" if lvl["price"] == d["level_price"] else ""
            lines.append(
                f"  ${lvl['price']:<8.0f} {lvl['label']}{hit}{marker}"
            )
        lines.append("")

    # Append options signal if available (level hit)
    opt_sig = d.get("options_signal")
    if opt_sig and not opt_sig.get("blocked"):
        lines.append("\u2500" * 30)
        lines.append("")
        if opt_sig.get("action") == "SELL_PUT":
            lines.append(format_naked_put_signal(opt_sig))
        elif opt_sig.get("action") == "SELL_CALL":
            lines.append(format_naked_call_signal(opt_sig))
        lines.append("")

    lines.append("Options window: 8:30 AM - 3:30 PM CST")
    return "\n".join(lines)


def format_vix_reversion_alert(alert: dict) -> str:
    """
    VIX Reversion alert — highest-conviction bounce signal.
    Fires when VIX spikes above 20 then drops back below 19.
    Backtest: Feb 5-6 pattern — VIX 21.8→20.4, SPY +1.34%, QQQ +1.58%.
    """
    d = alert
    spy_price = d["spx_price"] / 10
    lines = []

    # ── Header ────────────────────────────────────────────────
    lines.append(
        f"<b>VIX REVERSION</b>  |  VIX {d['vix_peak']:.1f} \u2192 {d['vix_price']:.1f}"
    )
    lines.append(
        f"SPX ${d['spx_price']:,.0f} ({d['change_pct']:+.1f}%)"
        f"  |  SPY ~${spy_price:.0f}"
        f"  |  {d['regime']}"
    )
    lines.append("")

    if d["blocked"]:
        for reason in d["block_reasons"]:
            lines.append(f"\u274c {reason}")
        lines.append("DO NOT TRADE")
        return "\n".join(lines)

    lines.append(
        "<b>HIGH CONVICTION BUY</b> — VIX fear spike reverting"
    )
    lines.append(
        f"VIX peaked {d['vix_peak']:.1f}, now crushing to {d['vix_price']:.1f}"
    )
    lines.append(
        "Backtest: VIX reversion = strongest bounce signal"
    )
    lines.append(
        "IV dropping = calls get cheaper AND underlying rises"
    )
    lines.append("")

    lines.append("<b>BUY CALLS (14+ DTE):</b>")
    for c in d.get("call_options", []):
        lines.append(f"{TOS} {c['ticker']} {c['strike']} ({c['label']})")
        lines.append(f"   {c['note']}")

    lines.append("")
    lines.append("This is the regime-shift entry. Size up.")
    lines.append("Entry: NOW if 8:30 AM-3:30 PM CST, otherwise at open tomorrow.")

    # Append naked put signal — this is the A+ conviction entry
    naked_put = d.get("naked_put")
    if naked_put and not naked_put.get("blocked"):
        lines.append("")
        lines.append("\u2500" * 30)
        lines.append("")
        lines.append(format_naked_put_signal(naked_put))

    return "\n".join(lines)


def format_tos_daily_intel(intel: dict) -> str:
    """
    Daily TOS intelligence report — morning pre-market brief for ThinkOrSwim.
    Read-only intel, no APPROVE/SKIP buttons.
    Keeps under 4096 chars (Telegram limit).
    """
    d = intel
    lines = []

    # ── Header ────────────────────────────────────────────────
    day_str = d.get("date_str", "")
    lines.append(f"{TOS} <b>TOS DAILY INTEL</b> | {day_str}")
    lines.append("")

    # ── Market snapshot ───────────────────────────────────────
    spx = d.get("spx_price", 0)
    vix = d.get("vix_price", 0)
    regime = d.get("regime", "?")
    futures_chg = d.get("futures_change_pct")
    snap = f"SPX {spx:,.0f} | VIX {vix:.1f} ({regime})"
    if futures_chg is not None:
        snap += f" | Futures {futures_chg:+.1f}%"
    lines.append(snap)

    # Expected weekly move from VIX
    weekly_move = d.get("expected_weekly_move")
    if weekly_move:
        lines.append(f"Expected weekly move: +/- {weekly_move:.0f} pts")
    lines.append("")

    # ── Support / Resistance from bracket data ────────────────
    levels = d.get("bracket_levels", [])
    if levels:
        lines.append("<b>SUPPORT / RESISTANCE (from brackets):</b>")
        for lvl in levels:
            label = lvl.get("label", "")
            price = lvl.get("price", 0)
            wr = lvl.get("win_rate")
            detail = f"  {price:,.0f}  {label}"
            if wr:
                detail += f" ({wr:.1%} NO WR)"
            lines.append(detail)
        lines.append("")

    # ── External trader intel ─────────────────────────────────
    ext = d.get("external_intel")
    if ext:
        source = ext.get("source", "Trader")
        lines.append(f"\U0001f4ca <b>TRADER INTEL ({source}):</b>")
        for lvl in ext.get("levels", [])[:8]:
            ticker = lvl.get("ticker", "")
            price = lvl.get("price", 0)
            note = lvl.get("note", "")
            lines.append(f"  {ticker}: {price:,.0f} — {note}")
        psych = ext.get("psychology")
        if psych:
            lines.append(f"  \u26a0\ufe0f {psych}")
        lines.append("")

    # ── Dip buy calls ─────────────────────────────────────────
    dip_level = d.get("dip_level")
    call_options = d.get("call_options", [])
    bounce_rate = d.get("bounce_rate")
    if call_options:
        dip_str = f" (to ~{dip_level:,.0f})" if dip_level else ""
        lines.append(f"<b>IF SPX DIPS -1.0%{dip_str}:</b>")
        for c in call_options:
            lines.append(f"  {TOS} Buy {c['ticker']} {c['strike']} ({c['label']})")
            if c.get("note"):
                lines.append(f"     {c['note']}")
        if bounce_rate:
            lines.append(f"  {bounce_rate:.0f}% bounce rate in {regime} regime")
        lines.append("")

    # ── Put credit spreads ────────────────────────────────────
    spreads = d.get("put_credit_spreads", [])
    if spreads:
        lines.append("<b>PUT CREDIT SPREADS (premium selling):</b>")
        for s in spreads:
            if s["instrument"] == "SPY":
                lines.append(f"  {TOS} {s['action']} (weekly) | ${s.get('risk', 0)} max risk")
            elif s.get("margin"):
                lines.append(f"  {TOS} {s['action']} {s['instrument']} | ~${s['margin']} margin")
            else:
                lines.append(f"  {TOS} {s['action']} {s['instrument']}")
        lines.append("")

    # ── Options playbook ────────────────────────────────────────
    options_intel = d.get("options_intel")
    if options_intel:
        playbook = format_options_daily_intel(options_intel)
        if playbook:
            lines.append(playbook)
            lines.append("")

    # ── Catalyst calendar ─────────────────────────────────────
    catalyst = d.get("catalyst")
    if catalyst:
        lines.append(f"<b>CATALYST TODAY:</b> {catalyst['name']} {catalyst.get('time', '')}")
        if catalyst.get("guidance"):
            lines.append(f"  {catalyst['guidance']}")
        lines.append("")

    # ── VIX reversion status ──────────────────────────────────
    vix_note = d.get("vix_note")
    if vix_note:
        lines.append(f"{TOS} {vix_note}")
        lines.append("")

    # ── Blocked warning ───────────────────────────────────────
    if d.get("blocked"):
        for reason in d.get("block_reasons", []):
            lines.append(f"\u274c {reason}")
        lines.append("")

    lines.append("<i>Read-only intel — place trades on TOS yourself</i>")

    return "\n".join(lines)


def format_spx_bracket_alert(alert: dict) -> str:
    """
    Format SPX bracket scan results.
    Shows top trade recommendations with exact Kalshi tickers and sizing.
    Backed by 10,000-market analysis: 94.7% NO WR in sweet spot.
    """
    d = alert
    lines = []

    # ── Header ────────────────────────────────────────────────
    catalyst = " | CPI DAY" if d["is_catalyst_day"] else ""
    lines.append(
        f"<b>SPX BRACKET SCAN</b>{catalyst}"
    )
    lines.append(
        f"SPX ${d['spx_price']:,.0f} ({d['change_pct']:+.1f}%)"
        f"  |  VIX {d['vix_price']:.1f} ({d['regime']})"
    )
    lines.append(
        f"Scanned: {d['total_markets']} markets"
        f" | {d['sweet_spot_count']} in sweet spot"
        f" | Balance: ${d['balance']:.0f}"
    )
    lines.append("")

    # ── Edge Summary ──────────────────────────────────────────
    lines.append(
        "<b>94.7% NO win rate</b> on brackets priced 10-49c YES"
    )
    lines.append(
        "SPX lands in any 25-pt bracket only 5.9% of the time"
    )
    lines.append("")

    # ── Trade Recommendations ─────────────────────────────────
    trades = d.get("trades", [])
    if not trades:
        lines.append("No trades in sweet spot right now.")
        lines.append("Check back after market moves.")
        return "\n".join(lines)

    total_cost = 0
    total_profit = 0

    auto = d.get("auto_executed", False)
    if auto:
        filled = d.get("filled_count", 0)
        attempted = d.get("total_attempted", 0)
        lines.append(f"<b>AUTO-EXECUTED: {filled}/{attempted} orders filled</b>")
    else:
        lines.append(f"<b>TOP {len(trades)} TRADES:</b>")
    lines.append("")

    for i, t in enumerate(trades, 1):
        if t.get("action") == "SKIP":
            continue

        bracket_label = f"${t['bracket_low']:,.0f}-${t['bracket_high']:,.0f}"
        distance = t.get("distance", 0)

        # Execution status marker
        exec_result = t.get("execution", {})
        if exec_result.get("status") == "filled":
            status_mark = "\u2705"
        elif exec_result.get("status") == "blocked":
            status_mark = "\U0001f6ab"
        elif exec_result.get("status") == "error":
            status_mark = "\u274c"
        else:
            status_mark = KAL

        lines.append(
            f"{status_mark} <b>{i}. BUY NO</b> — SPX {bracket_label}"
        )
        lines.append(
            f"   {t['ticker']}"
        )
        lines.append(
            f"   YES @ {t['yes_price']}c"
            f" | NO cost ${t.get('cost_per_contract', 0):.2f}"
            f" | {t.get('contracts', 0)}x = ${t.get('total_cost', 0):.2f}"
        )
        lines.append(
            f"   {distance:.0f}pts away"
            f" | {t.get('win_rate', 0):.1%} WR"
            f" | +{t.get('edge', 0):.1%} edge"
            f" | Grade: {t.get('grade', '?')}"
        )

        if exec_result.get("status") == "blocked":
            lines.append(f"   BLOCKED: {exec_result.get('reason', '')}")
        elif exec_result.get("status") == "error":
            lines.append(f"   ERROR: {exec_result.get('error', '')[:60]}")

        if t.get("total_cost"):
            total_cost += t["total_cost"]
        if t.get("max_profit"):
            total_profit += t["max_profit"]

        lines.append("")

    # ── Summary ───────────────────────────────────────────────
    if total_cost > 0:
        avg_wr = 0.947
        lines.append(
            f"<b>TOTAL:</b> ${total_cost:.2f} deployed"
            f" \u2192 ${total_cost + total_profit:.2f} if all win"
            f" | 94.7% hist WR"
        )
    if auto:
        lines.append("")
        lines.append("Orders placed automatically. Monitor on Kalshi.")
    else:
        lines.append("")
        lines.append("Place orders on Kalshi. BUY NO on each bracket.")
    lines.append(f"Scan time: {d.get('scan_time', 'now')}")

    return "\n".join(lines)


# ── Options Signal Formatters (Naked Puts / Naked Calls) ──────


def format_naked_put_signal(signal: dict) -> str:
    """
    Format a naked put signal for Telegram.
    Includes: grade badge, trade details, WHY, EXIT PLAN, MAX RISK, psychology.
    """
    if not signal or signal.get("blocked"):
        reasons = signal.get("block_reasons", []) if signal else []
        if reasons:
            return "\n".join(["\u274c NAKED PUT BLOCKED"] + reasons)
        return ""

    s = signal
    grade = s.get("conviction_grade", "?")
    lines = []

    lines.append(
        f"{TOS} <b>SELL {s['ticker']} {s['strike']:.0f}P [{grade}]</b>"
    )
    lines.append(
        f"   {s.get('expiry_label', '?')} exp"
        f" | ~${s.get('premium_estimate', 0):.2f} premium"
        f" | {s.get('contracts', 1)}x"
    )
    lines.append("")

    # WHY NOW
    reason = s.get("entry_reason", "")
    if reason:
        lines.append(f"<b>WHY:</b> {reason}")
        lines.append("")

    # EXIT PLAN
    profit = s.get("profit_target", 0)
    stop = s.get("stop_loss", 0)
    lines.append("<b>EXIT PLAN:</b>")
    lines.append(f"  \u2705 Buy back at ~${profit:.2f} (50% profit)")
    lines.append(f"  \u274c Cut at ~${stop:.2f} (2x premium stop)")
    lines.append(f"  \u23f0 Close by Wednesday if weekly")
    lines.append("")

    # MAX RISK
    max_risk = s.get("max_risk", 0)
    size_label = s.get("size_label", "")
    lines.append(
        f"<b>MAX RISK:</b> ${max_risk:.0f}"
        f"  |  {size_label}"
    )
    lines.append("")

    # Psychology
    psych = s.get("psychology_note", "")
    if psych:
        lines.append(f"<i>{psych}</i>")

    return "\n".join(lines)


def format_naked_call_signal(signal: dict) -> str:
    """
    Format a naked call signal for Telegram.
    Mirror of put signal but bearish direction.
    """
    if not signal or signal.get("blocked"):
        reasons = signal.get("block_reasons", []) if signal else []
        if reasons:
            return "\n".join(["\u274c NAKED CALL BLOCKED"] + reasons)
        return ""

    s = signal
    grade = s.get("conviction_grade", "?")
    lines = []

    lines.append(
        f"{TOS} <b>SELL {s['ticker']} {s['strike']:.0f}C [{grade}]</b>"
    )
    lines.append(
        f"   {s.get('expiry_label', '?')} exp"
        f" | ~${s.get('premium_estimate', 0):.2f} premium"
        f" | {s.get('contracts', 1)}x"
    )
    lines.append("")

    # WHY NOW
    reason = s.get("entry_reason", "")
    if reason:
        lines.append(f"<b>WHY:</b> {reason}")
        lines.append("")

    # EXIT PLAN
    profit = s.get("profit_target", 0)
    stop = s.get("stop_loss", 0)
    lines.append("<b>EXIT PLAN:</b>")
    lines.append(f"  \u2705 Buy back at ~${profit:.2f} (50% profit)")
    lines.append(f"  \u274c Cut at ~${stop:.2f} (2x premium stop)")
    lines.append(f"  \u23f0 Close by Wednesday if weekly")
    lines.append("")

    # MAX RISK
    max_risk = s.get("max_risk", 0)
    size_label = s.get("size_label", "")
    lines.append(
        f"<b>MAX RISK:</b> ${max_risk:.0f}"
        f"  |  {size_label}"
    )
    lines.append("")

    # Psychology
    psych = s.get("psychology_note", "")
    if psych:
        lines.append(f"<i>{psych}</i>")

    return "\n".join(lines)


def format_options_daily_intel(options_intel: dict) -> str:
    """
    Format OPTIONS PLAYBOOK section for the morning report.
    Shows: regime guidance, risk budget, put ideas, call ideas, rules reminder.
    """
    if not options_intel:
        return ""

    o = options_intel
    lines = []

    lines.append(f"{TOS} <b>OPTIONS PLAYBOOK</b>")
    lines.append("")

    # Regime guidance
    guidance = o.get("regime_guidance", "")
    if guidance:
        lines.append(guidance)
        lines.append("")

    # Risk budget
    budget = o.get("risk_budget", 0)
    if budget:
        per_trade = min(budget, 500)
        max_positions = max(1, int(budget / per_trade)) if per_trade else 1
        lines.append(
            f"<b>Risk budget:</b> ${budget:.0f} total today"
            f" | ${per_trade:.0f}/trade"
            f" | Max {max_positions} positions"
        )
        lines.append("")

    # Naked put ideas
    put_ideas = o.get("naked_put_ideas", [])
    if put_ideas:
        lines.append("<b>SELL PUTS (bullish):</b>")
        for idea in put_ideas[:3]:
            ticker = idea.get("ticker", "?")
            strike = idea.get("strike", 0)
            prem = idea.get("premium_estimate", 0)
            grade = idea.get("conviction_grade", "?")
            condition = idea.get("condition", "")
            lines.append(
                f"  {ticker} {strike:.0f}P [{grade}] ~${prem:.2f}"
            )
            if condition:
                lines.append(f"    IF: {condition}")
        lines.append("")

    # Naked call ideas
    call_ideas = o.get("naked_call_ideas", [])
    if call_ideas:
        lines.append("<b>SELL CALLS (bearish):</b>")
        for idea in call_ideas[:3]:
            ticker = idea.get("ticker", "?")
            strike = idea.get("strike", 0)
            prem = idea.get("premium_estimate", 0)
            grade = idea.get("conviction_grade", "?")
            condition = idea.get("condition", "")
            lines.append(
                f"  {ticker} {strike:.0f}C [{grade}] ~${prem:.2f}"
            )
            if condition:
                lines.append(f"    IF: {condition}")
        lines.append("")

    # Rules reminder
    lines.append("<i>50% profit target | 2x stop | Close Wed if weekly | No entries after 2:30 PM CST</i>")

    return "\n".join(lines)
