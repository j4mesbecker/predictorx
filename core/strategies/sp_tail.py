"""
PredictorX — S&P Tail Strategy
Wraps kalshi_data's VIX regime logic and tail probability tables.
VIX-regime gated S&P 500 tail risk analysis.
"""

import logging
from datetime import datetime, date

from core.strategies.base import Strategy
from core.models import Prediction
from config.constants import (
    TAIL_THRESHOLDS, TAIL_PROB, BLACKOUT_DATES,
    SP_TAIL_SHARE, VIX_REGIMES, TOS_ENABLED, TOS_INSTRUMENTS
)

logger = logging.getLogger(__name__)


class SPTailStrategy(Strategy):

    @property
    def name(self) -> str:
        return "sp_tail"

    @property
    def description(self) -> str:
        return "VIX-regime gated S&P 500 tail risk selling"

    async def is_available(self) -> bool:
        try:
            from adapters.kalshi_data import get_vix_module
            return get_vix_module() is not None
        except Exception:
            return True  # Can fall back to constants

    async def scan(self) -> list[Prediction]:
        """
        Analyze S&P tail markets based on current VIX regime.
        """
        predictions = []

        # Get current market data
        try:
            from adapters.kalshi_data import get_vix, get_spx, compute_tail_strikes
            vix_data = get_vix()
            spx_data = get_spx()
        except Exception as e:
            logger.warning(f"Could not fetch market data: {e}")
            return predictions

        vix_price = vix_data["price"]
        regime = vix_data["regime"]
        spx_price = spx_data["price"]

        # Check blackout
        today_str = date.today().strftime("%Y-%m-%d")
        is_blackout = today_str in BLACKOUT_DATES

        if is_blackout:
            logger.info(f"Blackout day ({today_str}) — no S&P tail predictions")
            return predictions

        if regime == "CRISIS":
            logger.info(f"VIX at {vix_price:.1f} (CRISIS) — no tail predictions")
            return predictions

        # Get eligible tails for this regime
        eligible = self._eligible_tails(regime)
        tail_strikes = compute_tail_strikes(spx_price)

        for pct in eligible:
            strike_info = next((s for s in tail_strikes if s["pct"] == pct), None)
            if not strike_info:
                continue

            # Historical probability of this drop
            hist_prob = TAIL_PROB.get(regime, TAIL_PROB["MEDIUM"]).get(int(pct), 0.05)
            win_prob = 1.0 - hist_prob

            # Estimate market price (Kalshi overprices tails ~3-5x)
            est_market_price = max(0.03, min(0.15, hist_prob * 4))

            # Edge = our probability of winning - what market charges
            # For selling tail risk: we're selling YES (betting drop WON'T happen)
            edge = est_market_price - hist_prob  # Positive = market overprices the risk

            pred = Prediction(
                strategy="sp_tail",
                market_ticker=f"INXD-{datetime.now().strftime('%d%b%y').upper()}-B{int(strike_info['strike'])}",
                market_title=f"S&P 500 drop >{pct}% today? (Strike: {strike_info['strike']:.0f})",
                platform="kalshi",
                predicted_probability=win_prob,
                calibrated_probability=win_prob,  # Tail probs are already calibrated from 25yr data
                market_price=est_market_price,
                edge=edge,
                confidence_score=0.0,  # Set by scoring layer
                side="no",  # We bet the drop WON'T happen
                vix_level=vix_price,
                vix_regime=regime,
                confidence_factors={
                    "regime": regime,
                    "vix_price": vix_price,
                    "spx_price": spx_price,
                    "pct_drop": pct,
                    "strike": strike_info["strike"],
                    "hist_prob": hist_prob,
                    "win_prob": win_prob,
                    "days_in_regime": 0,  # Could be enriched later
                },
            )

            factors = await self.get_confidence_factors(pred)
            pred.confidence_factors.update(factors)

            # Generate ThinkorSwim trade suggestions
            if TOS_ENABLED:
                tos = self._tos_suggestions(spx_price, strike_info["strike"], pct, regime)
                pred.confidence_factors["tos_trades"] = tos

            predictions.append(pred)

        # Sort by edge (highest first)
        predictions.sort(key=lambda p: abs(p.edge), reverse=True)
        return predictions

    def _eligible_tails(self, regime: str) -> list[float]:
        """Which tail thresholds are tradeable given the VIX regime."""
        if regime == "LOW":
            return [2.0, 3.0]
        elif regime == "LOW_MED":
            return [3.0, 5.0]
        elif regime == "MEDIUM":
            return [5.0]
        else:
            return []

    def _tos_suggestions(self, spx_price: float, kalshi_strike: float, pct_drop: float, regime: str) -> list[dict]:
        """
        Generate equivalent ThinkorSwim trade ideas for this tail thesis.
        Same directional bet (S&P won't drop X%), different instruments.
        """
        trades = []

        # SPY put credit spread (primary suggestion for $500 account)
        spy_info = TOS_INSTRUMENTS["SPY"]
        if spy_info["max_contracts"] > 0:
            spy_price = spx_price / 10  # SPY ≈ SPX / 10
            # Short put near the strike, long put $1 below
            short_strike = round(spy_price * (1 - pct_drop / 100), 0)
            long_strike = short_strike - spy_info["spread_width"]
            trades.append({
                "instrument": "SPY",
                "type": "Put Credit Spread (sell)",
                "short_strike": short_strike,
                "long_strike": long_strike,
                "expiry": "0DTE or weekly",
                "max_contracts": spy_info["max_contracts"],
                "max_risk": spy_info["max_risk_per_spread"] * spy_info["max_contracts"],
                "description": f"SELL {short_strike}p / BUY {long_strike}p SPY",
                "thesis": f"S&P won't drop >{pct_drop}% — VIX {regime}",
            })

        # /MES micro futures — if regime is very clear
        mes_info = TOS_INSTRUMENTS["/MES"]
        if mes_info["max_contracts"] > 0 and regime in ("LOW", "LOW_MED"):
            trades.append({
                "instrument": "/MES",
                "type": "Long micro futures (swing)",
                "max_contracts": mes_info["max_contracts"],
                "margin": mes_info["margin"],
                "multiplier": mes_info["multiplier"],
                "description": f"BUY 1 /MES @ ~{spx_price:.0f}",
                "thesis": f"Low-vol regime ({regime}) favors long bias — swing hold",
                "note": f"Margin ~${mes_info['margin']}. Only in LOW/LOW_MED regime.",
            })

        # /MNQ micro Nasdaq — if regime is LOW
        mnq_info = TOS_INSTRUMENTS["/MNQ"]
        if mnq_info["max_contracts"] > 0 and regime == "LOW":
            trades.append({
                "instrument": "/MNQ",
                "type": "Long micro Nasdaq futures (swing)",
                "max_contracts": mnq_info["max_contracts"],
                "margin": mnq_info["margin"],
                "multiplier": mnq_info["multiplier"],
                "description": "BUY 1 /MNQ",
                "thesis": "LOW VIX regime = risk-on, Nasdaq outperforms",
                "note": f"Margin ~${mnq_info['margin']}. Higher beta than /MES.",
            })

        return trades

    async def get_confidence_factors(self, prediction: Prediction) -> dict:
        """S&P tail-specific confidence factors."""
        factors = prediction.confidence_factors.copy()
        regime = factors.get("regime", "MEDIUM")
        pct = factors.get("pct_drop", 2.0)
        hist_prob = factors.get("hist_prob", 0.05)

        # Regime clarity (LOW regime = very clear signal)
        regime_confidence = {
            "LOW": 0.98,      # 0% loss rate in 25 years
            "LOW_MED": 0.85,  # Very low loss rate
            "MEDIUM": 0.60,   # Some risk
            "HIGH": 0.30,     # Risky
            "CRISIS": 0.0,    # Don't trade
        }
        factors["model_agreement"] = regime_confidence.get(regime, 0.5)

        # Historical accuracy (calibrated from 6,563 trading days)
        if hist_prob == 0.0:
            factors["historical_accuracy"] = 1.0
        elif hist_prob < 0.01:
            factors["historical_accuracy"] = 0.95
        elif hist_prob < 0.05:
            factors["historical_accuracy"] = 0.80
        else:
            factors["historical_accuracy"] = 0.50

        # Data quality (VIX data freshness)
        factors["data_quality"] = 0.90  # Live data

        return factors
