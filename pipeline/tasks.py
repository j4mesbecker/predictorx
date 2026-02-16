"""
PredictorX — Pipeline Tasks
Individual fetch, analyze, and settle tasks for the data pipeline.
"""

import json
import logging
from datetime import datetime, date, timedelta

from config.settings import get_settings
from core.models import Prediction, VixSnapshot, WhaleSignal, WeatherForecast
from db.repository import Repository

logger = logging.getLogger(__name__)

_repo: Repository | None = None


def _get_repo() -> Repository:
    global _repo
    if _repo is None:
        settings = get_settings()
        _repo = Repository(settings.database_sync_url)
    return _repo


# ── Weather Forecasts ──────────────────────────────────────


async def fetch_weather_forecasts():
    """Fetch 4-source weather ensemble for all Kalshi cities."""
    from config.constants import KALSHI_STATIONS

    logger.info("Fetching weather forecasts...")
    repo = _get_repo()
    count = 0

    for city_code, city_info in KALSHI_STATIONS.items():
        try:
            forecast = await _fetch_city_weather(city_code, city_info)
            if forecast:
                _save_weather_forecast(repo, forecast)
                count += 1
        except Exception as e:
            logger.error(f"Weather fetch error for {city_code}: {e}")

    logger.info(f"Fetched weather for {count}/{len(KALSHI_STATIONS)} cities")


async def _fetch_city_weather(city_code: str, city_info: dict) -> WeatherForecast | None:
    """Fetch weather from NWS (primary) with fallback sources."""
    import httpx

    forecast = WeatherForecast(
        city=city_code,
        forecast_date=date.today().isoformat(),
    )

    # NWS Grid Points for each city
    nws_grids = {
        "NYC": ("OKX", 33, 37), "CHI": ("LOT", 65, 76),
        "MIA": ("MFL", 76, 50), "PHI": ("PHI", 57, 97),
        "AUS": ("EWX", 156, 91), "DEN": ("BOU", 62, 60),
        "SFO": ("MTR", 85, 105),
    }

    grid = nws_grids.get(city_code)
    if not grid:
        return None

    settings = get_settings()

    async with httpx.AsyncClient(timeout=15) as client:
        # Source 1: NWS
        try:
            office, gx, gy = grid
            resp = await client.get(
                f"https://api.weather.gov/gridpoints/{office}/{gx},{gy}/forecast",
                headers={"User-Agent": settings.nws_user_agent},
            )
            if resp.status_code == 200:
                periods = resp.json()["properties"]["periods"]
                for p in periods:
                    if p.get("isDaytime", False):
                        forecast.nws_high = float(p["temperature"])
                        break
        except Exception as e:
            logger.debug(f"NWS fetch failed for {city_code}: {e}")

        # Source 2: Open-Meteo (free, no key)
        try:
            lat_lon = {
                "NYC": (40.78, -73.97), "CHI": (41.97, -87.90),
                "MIA": (25.79, -80.29), "PHI": (39.87, -75.24),
                "AUS": (30.19, -97.67), "DEN": (39.86, -104.67),
                "SFO": (37.62, -122.37),
            }
            lat, lon = lat_lon.get(city_code, (0, 0))
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat, "longitude": lon,
                    "daily": "temperature_2m_max",
                    "temperature_unit": "fahrenheit",
                    "timezone": "America/New_York",
                    "forecast_days": 3,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                highs = data.get("daily", {}).get("temperature_2m_max", [])
                if highs:
                    forecast.open_meteo_high = round(highs[0], 1)
        except Exception as e:
            logger.debug(f"Open-Meteo fetch failed for {city_code}: {e}")

        # Source 3: WeatherAPI.com (if key available)
        if settings.weatherapi_key:
            try:
                resp = await client.get(
                    "https://api.weatherapi.com/v1/forecast.json",
                    params={
                        "key": settings.weatherapi_key,
                        "q": city_info["location"],
                        "days": 3,
                    },
                )
                if resp.status_code == 200:
                    days = resp.json()["forecast"]["forecastday"]
                    if days:
                        forecast.weatherapi_high = days[0]["day"]["maxtemp_f"]
            except Exception as e:
                logger.debug(f"WeatherAPI fetch failed for {city_code}: {e}")

        # Source 4: VisualCrossing (if key available)
        if settings.visualcrossing_key:
            try:
                resp = await client.get(
                    f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/{city_info['location']}",
                    params={
                        "unitGroup": "us",
                        "key": settings.visualcrossing_key,
                        "contentType": "json",
                        "include": "days",
                    },
                )
                if resp.status_code == 200:
                    days = resp.json().get("days", [])
                    if days:
                        forecast.visualcrossing_high = days[0].get("tempmax")
            except Exception as e:
                logger.debug(f"VisualCrossing fetch failed for {city_code}: {e}")

    # Compute consensus
    sources = [
        v for v in [
            forecast.nws_high, forecast.open_meteo_high,
            forecast.weatherapi_high, forecast.visualcrossing_high,
        ] if v is not None
    ]

    if sources:
        forecast.consensus_high = round(sum(sources) / len(sources), 1)
        if len(sources) >= 2:
            spread = max(sources) - min(sources)
            forecast.source_agreement = max(0, 1.0 - spread / 10.0)
        else:
            forecast.source_agreement = 0.5

    return forecast


def _save_weather_forecast(repo: Repository, f: WeatherForecast):
    """Save weather forecast to database."""
    from db.models import WeatherForecastRecord
    with repo._session() as session:
        record = WeatherForecastRecord(
            city=f.city,
            forecast_date=date.fromisoformat(f.forecast_date),
            nws_high=f.nws_high,
            open_meteo_high=f.open_meteo_high,
            weatherapi_high=f.weatherapi_high,
            visualcrossing_high=f.visualcrossing_high,
            consensus_high=f.consensus_high,
            source_agreement=f.source_agreement,
            forecast_horizon_days=f.forecast_horizon_days,
            seasonal_bias=f.seasonal_bias,
            uhi_adjustment=f.uhi_adjustment,
        )
        session.add(record)
        session.commit()


# ── VIX / S&P Data ─────────────────────────────────────────


async def fetch_vix_data():
    """Fetch VIX level and S&P price, save snapshot."""
    logger.info("Fetching VIX data...")
    repo = _get_repo()

    try:
        from adapters.kalshi_data import get_vix, get_spx
        vix_data = get_vix()
        spx_data = get_spx()

        snapshot = VixSnapshot(
            price=vix_data["price"],
            regime=vix_data["regime"],
            spx_price=spx_data.get("price"),
            spx_change_pct=spx_data.get("change_pct"),
            source=vix_data.get("source", "adapter"),
        )
        repo.save_vix_snapshot(snapshot)
        logger.info(f"VIX: {snapshot.price:.1f} ({snapshot.regime})")

    except Exception as e:
        logger.error(f"VIX fetch error: {e}")


# ── Whale Activity ──────────────────────────────────────────


async def fetch_whale_activity():
    """Fetch whale trades from Polymarket copy bot."""
    logger.info("Fetching whale activity...")
    repo = _get_repo()

    try:
        from adapters.copy_bot import get_curated_whales
        whales = get_curated_whales()

        if not isinstance(whales, dict):
            logger.info("No whale data available")
            return

        count = 0
        for addr, info in whales.items():
            if not isinstance(info, dict):
                continue

            for trade in info.get("recent_trades", []):
                signal = WhaleSignal(
                    wallet_address=addr,
                    wallet_alias=info.get("alias", addr[:8]),
                    whale_category=info.get("category", "UNKNOWN"),
                    market_id=trade.get("market_id", ""),
                    market_name=trade.get("market", ""),
                    side=trade.get("side", ""),
                    amount_usd=trade.get("amount", 0),
                    price=trade.get("price"),
                )
                repo.save_whale_signal(signal)
                count += 1

        logger.info(f"Saved {count} whale signals")

    except Exception as e:
        logger.error(f"Whale fetch error: {e}")


# ── Market Holiday Calendar ────────────────────────────────

def _get_market_holidays() -> set[str]:
    """NYSE market holidays for 2026 (dates markets are closed)."""
    return {
        "2026-01-01",  # New Year's Day
        "2026-01-19",  # MLK Jr. Day
        "2026-02-17",  # Presidents Day (Mon Feb 17)
        "2026-04-03",  # Good Friday
        "2026-05-25",  # Memorial Day
        "2026-07-03",  # Independence Day (observed)
        "2026-09-07",  # Labor Day
        "2026-11-26",  # Thanksgiving
        "2026-12-25",  # Christmas
    }


# ── Prediction Generation ──────────────────────────────────


async def generate_predictions():
    """Run all strategies, store predictions, and auto-execute weather trades."""
    logger.info("Generating predictions...")
    repo = _get_repo()

    try:
        from core.registry import StrategyRegistry

        registry = StrategyRegistry()
        settings = get_settings()
        opportunities = await registry.scan_all(balance=settings.starting_capital)

        saved = 0
        weather_to_execute = []

        for opp in opportunities:
            pred = opp.prediction
            if pred.edge > 0 and pred.confidence_score >= 0.50:
                repo.save_prediction(pred)
                saved += 1

                # Collect weather trades for auto-execution (weekends/holidays only)
                if pred.strategy == "weather" and pred.confidence_score >= 0.60:
                    weather_to_execute.append(pred)

        logger.info(f"Generated {len(opportunities)} opportunities, saved {saved} predictions")

        # Auto-execute weather trades on Kalshi — weekends and market holidays only
        # SPX brackets run on trading days; weather fills the gaps
        if weather_to_execute:
            from datetime import date
            today = date.today()
            is_weekend = today.weekday() >= 5  # Saturday=5, Sunday=6
            is_market_holiday = today.strftime("%Y-%m-%d") in _get_market_holidays()
            if is_weekend or is_market_holiday:
                await _execute_weather_trades(weather_to_execute)
            else:
                logger.info(f"Skipping {len(weather_to_execute)} weather trades — weekday trading day (weather runs weekends only)")

    except Exception as e:
        logger.error(f"Prediction generation error: {e}")


async def _execute_weather_trades(predictions: list):
    """Send weather trade opportunities to Telegram for approval."""
    from telegram.trade_approvals import send_batch_for_approval

    max_weather_per_scan = 5

    # Sort by edge (highest first), take top N
    predictions.sort(key=lambda p: p.edge, reverse=True)
    top = predictions[:max_weather_per_scan]

    approval_trades = []
    for pred in top:
        side = pred.side
        market_price_cents = int(pred.market_price * 100)

        # Calculate contracts: max $15 per weather trade
        max_cost = 15.0
        if side == "no":
            cost_per_contract_cents = 100 - market_price_cents
        else:
            cost_per_contract_cents = market_price_cents

        if cost_per_contract_cents <= 0:
            continue

        contracts = max(1, int(max_cost / (cost_per_contract_cents / 100.0)))

        # Verify market exists on Kalshi and get live prices
        try:
            from pipeline.spx_bracket_scanner import _kalshi_get
            market_data = _kalshi_get(f"/markets/{pred.market_ticker}")
            if not market_data or not market_data.get("market"):
                continue
            live_market = market_data["market"]
            if side == "no":
                no_ask = live_market.get("no_ask", 0) or 0
                if no_ask > 0:
                    cost_per_contract_cents = no_ask
                    contracts = max(1, int(max_cost / (no_ask / 100.0)))
            else:
                yes_ask = live_market.get("yes_ask", 0) or 0
                if yes_ask > 0:
                    cost_per_contract_cents = yes_ask
                    contracts = max(1, int(max_cost / (yes_ask / 100.0)))
        except Exception as e:
            logger.debug(f"Could not verify weather market {pred.market_ticker}: {e}")
            continue

        city = pred.confidence_factors.get("city", "?")
        approval_trades.append({
            "ticker": pred.market_ticker,
            "side": side,
            "contracts": contracts,
            "price_cents": cost_per_contract_cents,
            "description": (
                f"{city} | {pred.market_title}"
                f" | {pred.edge:.1%} edge"
                f" | {pred.confidence_score:.0%} conf"
            ),
            "metadata": {
                "city": city,
                "edge": pred.edge,
                "confidence": pred.confidence_score,
                "win_rate": pred.confidence_factors.get("historical_win_rate", 0),
                "grade": pred.confidence_factors.get("edge_grade", ""),
                "consensus_high": pred.confidence_factors.get("consensus_high", 0),
                "close_time": live_market.get("close_time", ""),
            },
        })

    if approval_trades:
        await send_batch_for_approval(
            approval_trades, "weather",
            f"{len(approval_trades)} weather trades found with 60%+ confidence",
        )
        logger.warning(f"WEATHER: Sent {len(approval_trades)} trades for approval")


# ── Prediction Settlement ──────────────────────────────────


async def settle_predictions():
    """Check pending predictions against market outcomes."""
    logger.info("Settling predictions...")
    repo = _get_repo()

    try:
        pending = repo.get_pending_predictions()
        if not pending:
            logger.info("No pending predictions to settle")
            return

        settled = 0
        for record in pending:
            if record.expiry and record.expiry < datetime.utcnow():
                result = await _check_market_result(record.market_ticker)
                if result is not None:
                    outcome = "win" if (
                        (record.side == "yes" and result == "yes") or
                        (record.side == "no" and result == "no")
                    ) else "loss"

                    pnl = record.recommended_cost if outcome == "win" else -record.recommended_cost
                    repo.settle_prediction(record.id, outcome, result, pnl)
                    settled += 1

                    # Track realized losses for daily loss limit
                    if outcome == "loss" and pnl < 0:
                        try:
                            from pipeline.kalshi_executor import record_realized_loss
                            record_realized_loss(abs(pnl))
                        except Exception:
                            pass

        logger.info(f"Settled {settled}/{len(pending)} predictions")

    except Exception as e:
        logger.error(f"Settlement error: {e}")


async def _check_market_result(ticker: str) -> str | None:
    """Check if a Kalshi market has settled."""
    try:
        from adapters.kalshi_main import get_kalshi_client
        client = get_kalshi_client()
        if client is None:
            return None

        market = client.get_market(ticker)
        if market and market.get("result"):
            return market["result"].lower()
    except Exception:
        pass
    return None


# ── Daily Performance Snapshot ─────────────────────────────


async def daily_performance_snapshot():
    """Calculate and store daily performance metrics."""
    logger.info("Computing daily performance...")
    repo = _get_repo()

    try:
        from db.models import DailyPerformanceRecord, PredictionRecord
        with repo._session() as session:
            today = date.today()
            today_start = datetime.combine(today, datetime.min.time())
            today_end = datetime.combine(today, datetime.max.time())

            # Get today's settled predictions
            settled = session.query(PredictionRecord).filter(
                PredictionRecord.settled_at >= today_start,
                PredictionRecord.settled_at <= today_end,
            ).all()

            total = len(settled)
            wins = sum(1 for r in settled if r.outcome == "win")
            pnl = sum(r.pnl or 0 for r in settled)

            # Get latest VIX
            vix_record = repo.get_latest_vix()

            record = DailyPerformanceRecord(
                date=today,
                total_predictions=total,
                correct_predictions=wins,
                accuracy=wins / total if total > 0 else 0,
                hypothetical_pnl=round(pnl, 2),
                avg_vix=vix_record.vix_price if vix_record else None,
                vix_regime=vix_record.regime if vix_record else None,
            )

            # Calculate cumulative P&L
            prev = session.query(DailyPerformanceRecord).filter(
                DailyPerformanceRecord.date < today
            ).order_by(DailyPerformanceRecord.date.desc()).first()

            record.cumulative_pnl = round(
                (prev.cumulative_pnl if prev else 0) + pnl, 2
            )

            # Strategy breakdown
            for r in settled:
                strategy = r.strategy
                if strategy == "weather":
                    record.weather_predictions = (record.weather_predictions or 0) + 1
                    if r.outcome == "win":
                        record.weather_correct = (record.weather_correct or 0) + 1
                elif strategy == "sp_tail":
                    record.sp_tail_predictions = (record.sp_tail_predictions or 0) + 1
                    if r.outcome == "win":
                        record.sp_tail_correct = (record.sp_tail_correct or 0) + 1
                elif strategy == "bracket_arb":
                    record.arb_predictions = (record.arb_predictions or 0) + 1
                    if r.outcome == "win":
                        record.arb_correct = (record.arb_correct or 0) + 1

            if record.weather_predictions:
                record.weather_accuracy = record.weather_correct / record.weather_predictions
            if record.sp_tail_predictions:
                record.sp_tail_accuracy = record.sp_tail_correct / record.sp_tail_predictions

            session.merge(record)
            session.commit()

        logger.info(f"Daily performance: {total} predictions, {wins} wins, ${pnl:+.2f}")

    except Exception as e:
        logger.error(f"Daily performance error: {e}")


# ── Calibration Update ─────────────────────────────────────


async def update_calibration():
    """Recalculate calibration curves from settled predictions."""
    logger.info("Updating calibration data...")
    repo = _get_repo()

    try:
        from core.scoring.calibration import get_calibration_metrics
        from db.models import CalibrationSnapshotRecord

        metrics = get_calibration_metrics()

        with repo._session() as session:
            record = CalibrationSnapshotRecord(
                strategy="all",
                total_markets=metrics.get("total_markets", 0),
                ece=metrics.get("ece"),
                brier_score=metrics.get("brier_score"),
                predicted_bins=json.dumps(metrics.get("predicted_bins", [])),
                actual_rates=json.dumps(metrics.get("actual_rates", [])),
                sample_counts=json.dumps(metrics.get("sample_counts", [])),
            )
            session.add(record)
            session.commit()

        logger.info(f"Calibration updated: {metrics.get('total_markets', 0)} markets")

    except Exception as e:
        logger.error(f"Calibration update error: {e}")


# ── TOS Daily Intelligence Report ─────────────────────────


async def generate_tos_daily_intel():
    """
    Morning pre-market TOS intelligence report.
    Runs at 6:15 AM CST (7:15 AM ET) before market open.
    Read-only — no APPROVE/SKIP buttons, just actionable intel for ThinkOrSwim.

    Pulls: SPX price, VIX/regime, bracket support/resistance, dip-buy calls,
    put credit spreads, catalyst calendar, VIX reversion status.
    """
    from math import sqrt
    from config.constants import (
        BLACKOUT_DATES, BLACKOUT_LABELS, TAIL_WIN_RATES,
    )
    from pipeline.spx_monitor import compute_dip_buy_calls, compute_put_credit_spreads
    from pipeline.spx_bracket_scanner import _fetch_spx_price, _fetch_spx_brackets
    from telegram.formatters import format_tos_daily_intel
    from telegram.bot import get_bot

    bot = get_bot()
    if not bot.configured:
        return

    today = date.today()
    today_str = today.strftime("%Y-%m-%d")

    # Only weekdays
    if today.weekday() >= 5:
        return

    logger.info("Generating TOS daily intelligence report...")

    # ── Fetch SPX price ───────────────────────────────────────
    spx_data = _fetch_spx_price()
    if not spx_data or not spx_data.get("price"):
        logger.warning("TOS intel: no SPX price available")
        return

    spx_price = spx_data["price"]
    prev_close = spx_data.get("prev_close", spx_price)
    change_pct = ((spx_price - prev_close) / prev_close * 100) if prev_close else 0

    # ── Fetch VIX ─────────────────────────────────────────────
    vix_price = 0
    regime = "MEDIUM"
    try:
        from adapters.kalshi_data import get_vix
        vix_data = get_vix()
        vix_price = vix_data.get("price", 0)
        regime = vix_data.get("regime", "MEDIUM")
    except Exception:
        pass

    # ── Expected weekly move from VIX ─────────────────────────
    # VIX is annualized implied vol; weekly move = VIX / sqrt(52) * SPX / 100
    weekly_move = None
    if vix_price > 0 and spx_price > 0:
        weekly_move = (vix_price / 100) * spx_price / sqrt(52)

    # ── Bracket support/resistance ────────────────────────────
    bracket_levels = []
    try:
        markets = _fetch_spx_brackets()
        if markets:
            # Build support/resistance from bracket NO win rates
            # Brackets BELOW SPX = support, brackets ABOVE = resistance
            for m in markets:
                yes_price = m.get("yes_ask") or m.get("yes_bid", 0)
                if yes_price <= 0:
                    continue
                no_wr = (100 - yes_price) / 100.0  # NO win rate proxy
                distance = m["bracket_mid"] - spx_price

                if abs(distance) < 25:
                    continue  # Skip brackets too close to current price

                level = {
                    "price": m["bracket_mid"],
                    "bracket_low": m["bracket_low"],
                    "bracket_high": m["bracket_high"],
                    "win_rate": no_wr if no_wr >= 0.80 else None,
                    "yes_price": yes_price,
                }

                if distance < 0 and no_wr >= 0.85:
                    level["label"] = "Support"
                    if no_wr >= 0.94:
                        level["label"] = "Strong support"
                    bracket_levels.append(level)
                elif distance > 0 and no_wr >= 0.85:
                    level["label"] = "Resistance"
                    if no_wr >= 0.94:
                        level["label"] = "Strong resistance"
                    bracket_levels.append(level)

            # Sort: support descending, resistance ascending — closest to SPX first
            supports = sorted(
                [l for l in bracket_levels if "support" in l["label"].lower()],
                key=lambda x: x["price"], reverse=True,
            )[:3]
            resistances = sorted(
                [l for l in bracket_levels if "resistance" in l["label"].lower()],
                key=lambda x: x["price"],
            )[:3]
            bracket_levels = supports + resistances
    except Exception as e:
        logger.debug(f"TOS intel bracket fetch: {e}")

    # ── Dip buy call options ──────────────────────────────────
    dip_level = spx_price * 0.99  # -1% dip target
    call_options = compute_dip_buy_calls(spx_price, today)

    # Bounce rate from regime
    bounce_rates = {"LOW": 98, "LOW_MED": 98, "MEDIUM": 95, "HIGH": 85, "CRISIS": 70}
    bounce_rate = bounce_rates.get(regime, 95)

    # ── Put credit spreads ────────────────────────────────────
    put_credit_spreads = compute_put_credit_spreads(spx_price, 1.0, regime)

    # ── Catalyst calendar ─────────────────────────────────────
    catalyst = None
    if today_str in BLACKOUT_DATES:
        catalyst = BLACKOUT_LABELS.get(today_str, {
            "name": "FOMC/CPI/NFP", "time": "", "guidance": "Wait for data release before entering",
        })

    # ── VIX reversion note ────────────────────────────────────
    vix_note = None
    if vix_price >= 20:
        vix_note = f"VIX at {vix_price:.1f} (>20) — watch for reversion below 19 = HIGH CONVICTION BUY CALLS"
    elif vix_price >= 18:
        vix_note = f"VIX {vix_price:.1f} — elevated, reversion armed if spikes >20 then drops <19"

    # ── External trader intel (Brando/EliteOptions) ─────────
    external_intel = None
    try:
        from pathlib import Path
        intel_path = Path("data/external_intel/brando_levels.json")
        if intel_path.exists():
            with open(intel_path) as f:
                ext = json.load(f)
            if ext.get("date") == today_str:
                external_intel = ext
    except Exception as e:
        logger.debug(f"External intel load: {e}")

    # ── Options playbook (naked puts/calls intel) ─────────────
    options_intel = None
    try:
        from core.strategies.options_strategy import compute_daily_options_intel
        brando_for_options = None
        if external_intel and external_intel.get("levels"):
            brando_for_options = external_intel["levels"]
        options_intel = compute_daily_options_intel(
            spx_price=spx_price,
            vix_price=vix_price,
            regime=regime,
            brando_levels=brando_for_options,
            bracket_levels=bracket_levels if bracket_levels else None,
        )
    except Exception as e:
        logger.debug(f"Options intel computation error: {e}")

    # ── Blocked? ──────────────────────────────────────────────
    blocked = False
    block_reasons = []
    if regime in ("HIGH", "CRISIS"):
        blocked = True
        block_reasons.append(f"VIX {regime} — reduce size, no aggressive entries")
    if catalyst:
        block_reasons.append(f"{catalyst['name']} today — {catalyst.get('guidance', 'wait for data')}")

    # ── Build intel dict ──────────────────────────────────────
    day_label = today.strftime("%a %b %d")
    intel = {
        "date_str": day_label,
        "spx_price": spx_price,
        "vix_price": vix_price,
        "regime": regime,
        "futures_change_pct": round(change_pct, 1) if change_pct != 0 else None,
        "expected_weekly_move": round(weekly_move) if weekly_move else None,
        "bracket_levels": bracket_levels,
        "dip_level": round(dip_level),
        "call_options": call_options,
        "bounce_rate": bounce_rate,
        "put_credit_spreads": put_credit_spreads,
        "catalyst": catalyst,
        "vix_note": vix_note,
        "blocked": blocked,
        "block_reasons": block_reasons,
        "external_intel": external_intel,
        "options_intel": options_intel,
    }

    # ── Format and send ───────────────────────────────────────
    text = format_tos_daily_intel(intel)
    await bot.send_message(text)
    logger.info(f"TOS daily intel sent: SPX {spx_price:,.0f} VIX {vix_price:.1f} ({regime})")
