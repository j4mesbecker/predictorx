"""
PredictorX — Telegram Command Handlers
All /command handlers for the bot.
"""

import logging
from datetime import datetime

from telegram.bot import get_bot
from telegram.formatters import (
    format_morning_scan, format_tail_analysis,
    format_weather_predictions, format_performance_summary,
    format_status,
)
from core.registry import StrategyRegistry
from core.models import VixSnapshot
from config.settings import get_settings
from adapters.paths import verify_paths

logger = logging.getLogger(__name__)

# Shared registry instance
_registry = None


def get_registry() -> StrategyRegistry:
    global _registry
    if _registry is None:
        _registry = StrategyRegistry()
    return _registry


def register_all_commands():
    """Register all command handlers with the bot."""
    bot = get_bot()

    @bot.command("help")
    async def cmd_help(chat_id: str, args: str):
        text = (
            "<b>\U0001f916 PredictorX Commands</b>\n\n"
            "/scan \u2014 Run fresh scan, show top opportunities\n"
            "/wscan \u2014 Scan live Kalshi weather markets for trades\n"
            "/weather [city] \u2014 Weather prediction details\n"
            "/tails \u2014 S&P tail analysis with VIX regime\n"
            "/whales \u2014 Top Polymarket trader activity\n"
            "/performance \u2014 P&L and accuracy stats\n"
            "/calibration \u2014 Model calibration report\n"
            "/status \u2014 System health and data freshness\n"
            "/help \u2014 Show this message"
        )
        await bot.send_message(text, chat_id=chat_id)

    @bot.command("scan")
    async def cmd_scan(chat_id: str, args: str):
        await bot.send_message("\u23f3 Running scan...", chat_id=chat_id)

        try:
            registry = get_registry()
            settings = get_settings()
            opportunities = await registry.scan_all(balance=settings.starting_capital)

            # Get VIX data for context
            vix = None
            try:
                from adapters.kalshi_data import get_vix, get_spx
                vix_data = get_vix()
                spx_data = get_spx()
                vix = VixSnapshot(
                    price=vix_data["price"],
                    regime=vix_data["regime"],
                    spx_price=spx_data.get("price"),
                )
            except Exception:
                pass

            text = format_morning_scan(opportunities, vix)
            await bot.send_message(text, chat_id=chat_id)

        except Exception as e:
            logger.error(f"Scan error: {e}")
            await bot.send_message(f"Scan failed: {e}", chat_id=chat_id)

    @bot.command("tails")
    async def cmd_tails(chat_id: str, args: str):
        try:
            registry = get_registry()
            settings = get_settings()
            predictions = await registry.scan_strategy("sp_tail", balance=settings.starting_capital)

            vix = None
            try:
                from adapters.kalshi_data import get_vix, get_spx
                vix_data = get_vix()
                spx_data = get_spx()
                vix = VixSnapshot(
                    price=vix_data["price"],
                    regime=vix_data["regime"],
                    spx_price=spx_data.get("price"),
                )
            except Exception:
                pass

            text = format_tail_analysis(predictions, vix)
            await bot.send_message(text, chat_id=chat_id)

        except Exception as e:
            await bot.send_message(f"Tail analysis error: {e}", chat_id=chat_id)

    @bot.command("weather")
    async def cmd_weather(chat_id: str, args: str):
        try:
            registry = get_registry()
            settings = get_settings()
            predictions = await registry.scan_strategy("weather", balance=settings.starting_capital)

            if args:
                city = args.strip().upper()
                predictions = [p for p in predictions if p.confidence_factors.get("city") == city]

            text = format_weather_predictions(predictions)
            await bot.send_message(text, chat_id=chat_id)

        except Exception as e:
            await bot.send_message(f"Weather analysis error: {e}", chat_id=chat_id)

    @bot.command("wscan")
    async def cmd_wscan(chat_id: str, args: str):
        """Scan live Kalshi weather markets for NO sweet spot trades."""
        try:
            await bot.send_message("\u23f3 Scanning live Kalshi weather markets...", chat_id=chat_id)
            from pipeline.weather_scanner import scan_weather_markets
            await scan_weather_markets(force=True)
            # If no trades were sent for approval, let user know
        except Exception as e:
            await bot.send_message(f"Weather scan error: {e}", chat_id=chat_id)

    @bot.command("whales")
    async def cmd_whales(chat_id: str, args: str):
        try:
            from adapters.copy_bot import get_curated_whales
            whales = get_curated_whales()

            if isinstance(whales, dict) and whales:
                lines = ["<b>\U0001f40b WHALE INTELLIGENCE</b>", ""]
                count = 0
                for addr, info in list(whales.items())[:10]:
                    if isinstance(info, dict):
                        alias = info.get("alias", addr[:8])
                        category = info.get("category", "?")
                        pnl = info.get("pnl", 0)
                        lines.append(f"\u2022 <b>{alias}</b> ({category}) \u2014 ${pnl:,.0f} PnL")
                        count += 1

                if count == 0:
                    lines.append("Whale data loaded but no profiles available.")
                text = "\n".join(lines)
            else:
                text = (
                    "<b>\U0001f40b WHALE INTELLIGENCE</b>\n\n"
                    "Whale tracking module not fully connected.\n"
                    "Data will populate once the pipeline runs."
                )

            await bot.send_message(text, chat_id=chat_id)

        except Exception as e:
            await bot.send_message(f"Whale data error: {e}", chat_id=chat_id)

    @bot.command("performance")
    async def cmd_performance(chat_id: str, args: str):
        try:
            from db.repository import Repository
            settings = get_settings()
            repo = Repository(settings.database_sync_url)
            perf = repo.get_performance_summary(days=30)
            text = format_performance_summary(perf)
            await bot.send_message(text, chat_id=chat_id)
        except Exception as e:
            await bot.send_message(f"Performance data error: {e}", chat_id=chat_id)

    @bot.command("calibration")
    async def cmd_calibration(chat_id: str, args: str):
        try:
            from core.scoring.calibration import get_calibration_metrics
            metrics = get_calibration_metrics()

            lines = ["<b>\U0001f4ca CALIBRATION REPORT</b>", ""]
            lines.append(f"Markets Analyzed: {metrics.get('total_markets', 0)}")
            lines.append(f"Full Data: {'Yes' if metrics.get('has_full_data') else 'No'}")

            bias = metrics.get("city_bias", {})
            if bias:
                lines.append("")
                lines.append("<b>City Bias:</b>")
                for city, b in bias.items():
                    lines.append(f"  {city}: {b:+.4f}")

            await bot.send_message("\n".join(lines), chat_id=chat_id)
        except Exception as e:
            await bot.send_message(f"Calibration error: {e}", chat_id=chat_id)

    @bot.command("status")
    async def cmd_status(chat_id: str, args: str):
        paths = verify_paths()
        adapters = {}
        for name, exists in paths.items():
            # Simplify names
            short = name.replace("_root", "").replace("_src", "").replace("_", " ").title()
            if short not in adapters or exists:
                adapters[short] = exists

        text = format_status({
            "last_scan": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "active_predictions": 0,
            "pipeline_status": "Running" if True else "Stopped",
            "web_status": f"http://127.0.0.1:{get_settings().web_port}",
            "adapters": adapters,
        })
        await bot.send_message(text, chat_id=chat_id)

    logger.info("All Telegram commands registered")
