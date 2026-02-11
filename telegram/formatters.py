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
    Tells you exactly what happened and what to trade right now.
    """
    d = alert
    lines = []

    # ── Header: what just happened ───────────────────────────
    lines.append(
        f"<b>SPX {d['change_pct']:+.1f}%</b>  ${d['spx_price']:,.0f}"
        f"  |  VIX {d['vix_price']:.1f} ({d['regime']})"
    )
    lines.append("")

    # ── Blocked? Show why and stop ───────────────────────────
    if d["blocked"]:
        for reason in d["block_reasons"]:
            lines.append(f"\u274c {reason}")
        lines.append("DO NOT TRADE")
        return "\n".join(lines)

    # ── Backtest stats — why this trade works ────────────────
    drop = d["drop_pct"]
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

    # Cluster warning (trade is still allowed, just flagged)
    if d["cluster_warning"]:
        lines.append("\u26a0\ufe0f Clustering: yesterday was a big drop — elevated risk")

    lines.append("")

    # ── Trades to place NOW ──────────────────────────────────
    lines.append("<b>TRADE:</b>")

    # Kalshi
    lines.append(f"{KAL} SELL YES on >{drop:.0f}% drop — collect premium")

    # ToS
    for t in d["tos_trades"]:
        if t["instrument"] == "SPY":
            lines.append(f"{TOS} {t['action']} SPY | ${t['risk']} risk")
        else:
            margin = f" | ~${t['margin']} margin" if t.get("margin") else ""
            lines.append(f"{TOS} {t['action']} {t['instrument']}{margin}")

    return "\n".join(lines)
