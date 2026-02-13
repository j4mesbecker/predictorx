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

                # Collect weather trades for auto-execution
                if pred.strategy == "weather" and pred.confidence_score >= 0.60:
                    weather_to_execute.append(pred)

        logger.info(f"Generated {len(opportunities)} opportunities, saved {saved} predictions")

        # Auto-execute weather trades on Kalshi
        if weather_to_execute:
            await _execute_weather_trades(weather_to_execute)

    except Exception as e:
        logger.error(f"Prediction generation error: {e}")


async def _execute_weather_trades(predictions: list):
    """Auto-execute weather predictions on Kalshi."""
    from pipeline.kalshi_executor import place_order, send_trade_notification, get_balance

    balance = get_balance()
    executed = 0
    max_weather_per_scan = 5  # Don't flood with weather trades

    # Sort by edge (highest first), take top N
    predictions.sort(key=lambda p: p.edge, reverse=True)
    top = predictions[:max_weather_per_scan]

    for pred in top:
        side = pred.side
        market_price_cents = int(pred.market_price * 100)

        # Calculate contracts: max $15 per weather trade (more conservative than SPX)
        max_cost = 15.0
        if side == "no":
            cost_per_contract_cents = 100 - market_price_cents
        else:
            cost_per_contract_cents = market_price_cents

        if cost_per_contract_cents <= 0:
            continue

        cost_per_contract = cost_per_contract_cents / 100.0
        contracts = max(1, int(max_cost / cost_per_contract))

        # Verify the market exists on Kalshi before trying to trade
        try:
            from pipeline.spx_bracket_scanner import _kalshi_get
            market_data = _kalshi_get(f"/markets/{pred.market_ticker}")
            if not market_data or not market_data.get("market"):
                logger.debug(f"Weather market {pred.market_ticker} not found on Kalshi, skipping")
                continue
            # Use live price instead of estimated price
            live_market = market_data["market"]
            if side == "no":
                live_yes_ask = live_market.get("yes_ask", 0)
                if live_yes_ask > 0:
                    cost_per_contract_cents = 100 - live_yes_ask
                    cost_per_contract = cost_per_contract_cents / 100.0
                    contracts = max(1, int(max_cost / cost_per_contract))
                    market_price_cents = live_yes_ask
            else:
                live_yes_ask = live_market.get("yes_ask", 0)
                if live_yes_ask > 0:
                    cost_per_contract_cents = live_yes_ask
                    cost_per_contract = cost_per_contract_cents / 100.0
                    contracts = max(1, int(max_cost / cost_per_contract))
                    market_price_cents = live_yes_ask
        except Exception as e:
            logger.debug(f"Could not verify weather market {pred.market_ticker}: {e}")
            continue

        city = pred.confidence_factors.get("city", "?")
        result = place_order(
            ticker=pred.market_ticker,
            side=side,
            contracts=contracts,
            price_cents=cost_per_contract_cents,
            strategy="weather",
            metadata={
                "city": city,
                "edge": pred.edge,
                "confidence": pred.confidence_score,
                "win_rate": pred.confidence_factors.get("historical_win_rate", 0),
                "grade": pred.confidence_factors.get("edge_grade", ""),
                "consensus_high": pred.confidence_factors.get("consensus_high", 0),
            },
        )

        extra = (
            f"{city} | {pred.market_title}"
            f" | {pred.edge:.1%} edge"
            f" | {pred.confidence_score:.0%} conf"
        )
        await send_trade_notification(result, "weather", extra)

        if result.get("status") == "filled":
            executed += 1

    if executed > 0:
        logger.warning(f"WEATHER AUTO-EXEC: {executed}/{len(top)} orders filled")


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
