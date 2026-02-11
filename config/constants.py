"""
PredictorX — Hard-coded constants and risk limits.
These require source code changes to modify (intentional friction).
"""

# ── VIX Regime Thresholds ─────────────────────────────────
VIX_LOW = 15
VIX_LOW_MED = 20
VIX_MEDIUM = 25
VIX_HIGH = 35

VIX_REGIMES = {
    "LOW": {"max_vix": 15, "budget_pct": 0.03, "label": "Full deployment"},
    "LOW_MED": {"max_vix": 20, "budget_pct": 0.02, "label": "Reduced deployment"},
    "MEDIUM": {"max_vix": 25, "budget_pct": 0.015, "label": "Minimal tails"},
    "HIGH": {"max_vix": 35, "budget_pct": 0.005, "label": "Weather/arb only"},
    "CRISIS": {"max_vix": 999, "budget_pct": 0.0, "label": "ALL CASH"},
}

# ── Position Sizing Limits (Aggressive Growth Mode) ──────
# Tuned for fastest safe $500 → $5K growth
# Weather backtest achieved 12.2% daily compound ($314→$11.9K in 31 days)
HARD_BALANCE_FLOOR = 75.0        # Lower floor to keep trading longer in drawdowns
MAX_SINGLE_TRADE = 50.0          # 10% of $500 — doubled from 5%
DAILY_DEPLOYMENT_CAP = 200.0     # 40% of $500 — aggressive but survivable
MAX_OPEN_POSITIONS = 20          # More concurrent positions = more volume
MIN_EDGE_PCT = 0.05              # 5% minimum edge (was 8%) — captures more trades
KELLY_FRACTION = 0.40            # 40% of full Kelly (was 25%) — bigger sizing
MIN_CONTRACTS = 1                # At least 1 contract

# ── Dynamic Scaling ─────────────────────────────────────
# As balance grows, limits scale proportionally
# This keeps deployment % constant as we compound
DYNAMIC_SIZING = True
SIZING_BASE_BALANCE = 500.0      # Base for percentage calculations
MAX_SINGLE_TRADE_PCT = 0.10      # 10% of current balance
DAILY_DEPLOYMENT_PCT = 0.40      # 40% of current balance
GROWTH_TIERS = {
    # balance_threshold: {adjustments}
    500:  {"kelly": 0.40, "deploy_pct": 0.40, "max_trade_pct": 0.10},
    1000: {"kelly": 0.38, "deploy_pct": 0.38, "max_trade_pct": 0.10},
    2500: {"kelly": 0.35, "deploy_pct": 0.35, "max_trade_pct": 0.08},
    5000: {"kelly": 0.30, "deploy_pct": 0.30, "max_trade_pct": 0.07},
}

# ── ThinkorSwim / Schwab Instruments ────────────────────
# Same tail thesis expressed on different instruments via ToS
TOS_ENABLED = True
TOS_INSTRUMENTS = {
    "SPY": {
        "type": "etf_options",
        "multiplier": 100,
        "spread_width": 1,         # $1 wide put credit spreads
        "max_risk_per_spread": 100, # $100 max loss per 1-lot
        "max_contracts": 1,         # 1 spread at $500 account
        "min_credit": 0.10,         # Min $0.10 credit ($10/spread)
        "description": "SPY put credit spread",
    },
    "SPX": {
        "type": "index_options",
        "multiplier": 100,
        "spread_width": 5,         # $5 wide put credit spreads
        "max_risk_per_spread": 500, # Too large for $500 account
        "max_contracts": 0,         # Skip at this account size
        "min_credit": 0.50,
        "description": "SPX put credit spread (cash-settled)",
    },
    "/ES": {
        "type": "futures",
        "multiplier": 50,           # $50 per point
        "max_contracts": 0,          # $500 account — futures margin too high
        "description": "E-mini S&P futures (margin ~$12k)",
    },
    "/MES": {
        "type": "micro_futures",
        "multiplier": 5,             # $5 per point
        "max_contracts": 1,          # Micro is feasible at $500
        "margin": 1500,              # ~$1,500 margin
        "description": "Micro E-mini S&P futures",
    },
    "/NQ": {
        "type": "futures",
        "multiplier": 20,            # $20 per point
        "max_contracts": 0,           # Too large
        "description": "E-mini Nasdaq futures (margin ~$18k)",
    },
    "/MNQ": {
        "type": "micro_futures",
        "multiplier": 2,              # $2 per point
        "max_contracts": 1,
        "margin": 1800,
        "description": "Micro E-mini Nasdaq futures",
    },
}

# ── S&P Tail Thresholds ──────────────────────────────────
TAIL_THRESHOLDS = [
    {"pct": 2.0, "label": ">2% drop", "min_yes": 0.03, "max_yes": 0.10},
    {"pct": 3.0, "label": ">3% drop", "min_yes": 0.02, "max_yes": 0.07},
    {"pct": 5.0, "label": ">5% drop", "min_yes": 0.01, "max_yes": 0.04},
]

# ── Budget Allocation (Growth Mode) ──────────────────────
# Weather is the proven daily compounder ($314→$11.9K in 31 days)
# S&P tails are event-driven bonus income
SP_TAIL_SHARE = 0.30
WEATHER_SHARE = 0.55             # Weather gets majority — proven daily edge
ARB_SHARE = 0.15

# ── Kalshi Cities ─────────────────────────────────────────
KALSHI_STATIONS = {
    "NYC": {"station": "KNYC", "location": "Central Park", "type": "urban"},
    "CHI": {"station": "KORD", "location": "O'Hare Airport", "type": "airport"},
    "MIA": {"station": "KMIA", "location": "Miami Intl", "type": "airport"},
    "PHI": {"station": "KPHL", "location": "Philly Intl", "type": "airport"},
    "AUS": {"station": "KAUS", "location": "Austin-Bergstrom", "type": "airport"},
    "DEN": {"station": "KDEN", "location": "Denver Intl", "type": "airport"},
    "SFO": {"station": "KSFO", "location": "SFO Intl", "type": "airport"},
}

# ── Confidence Scoring Weights ────────────────────────────
CONFIDENCE_WEIGHTS = {
    "model_agreement": 0.30,
    "historical_accuracy": 0.25,
    "edge_magnitude": 0.20,
    "data_quality": 0.15,
    "whale_alignment": 0.10,
}

# ── FOMC / CPI / NFP Blackout Dates ──────────────────────
BLACKOUT_DATES = [
    "2026-01-28", "2026-01-29",
    "2026-02-13",
    "2026-03-12", "2026-03-17", "2026-03-18",
    "2026-04-10",
    "2026-05-05", "2026-05-06",
    "2026-06-16", "2026-06-17",
    "2026-07-28", "2026-07-29",
    "2026-09-15", "2026-09-16",
    "2026-11-03", "2026-11-04",
    "2026-12-15", "2026-12-16",
]

# ── Historical Tail Probabilities by VIX Regime ───────────
# From 6,563-day backtest (2000-2026)
TAIL_PROB = {
    "LOW":     {1: 0.0205, 2: 0.0000, 3: 0.0000, 5: 0.0000},
    "LOW_MED": {1: 0.1313, 2: 0.0202, 3: 0.0012, 5: 0.0000},
    "MEDIUM":  {1: 0.1313, 2: 0.0202, 3: 0.0012, 5: 0.0000},
    "HIGH":    {1: 0.3336, 2: 0.1736, 3: 0.0748, 5: 0.0159},
    "CRISIS":  {1: 0.3336, 2: 0.1736, 3: 0.0748, 5: 0.0159},
}

# ── Backtest Sample Sizes by Regime ─────────────────────────
# How many trading days back each regime stat
REGIME_SAMPLE_DAYS = {
    "LOW": 2093, "LOW_MED": 1121, "MEDIUM": 2093,
    "HIGH": 1256, "CRISIS": 1256,
}

# ── Win Rates (1 - loss probability) ────────────────────────
# Selling YES (betting the drop WON'T happen)
TAIL_WIN_RATES = {
    "LOW":     {2: 1.0000, 3: 1.0000, 5: 1.0000},  # 0 losses in 2,093 days
    "LOW_MED": {2: 0.9798, 3: 0.9988, 5: 1.0000},
    "MEDIUM":  {2: 0.9798, 3: 0.9988, 5: 1.0000},
    "HIGH":    {2: 0.8264, 3: 0.9252, 5: 0.9841},
    "CRISIS":  {2: 0.8264, 3: 0.9252, 5: 0.9841},
}

# ── Tail Clustering Risk ────────────────────────────────────
# P(big drop today | big drop yesterday) — from 6,563-day analysis
# After a crash, next-day tail risk spikes dramatically
CLUSTER_MULTIPLIER = {
    1: 1.42,   # 42% more likely after a >1% drop
    2: 2.79,   # 179% more likely after a >2% drop
    3: 6.83,   # 583% more likely after a >3% drop (MASSIVE)
}

# ── Monthly Tail Risk (relative to average) ─────────────────
# >1 = worse than average month, <1 = safer than average month
MONTHLY_RISK_FACTOR = {
    1: 1.12, 2: 1.07, 3: 1.19,   # Jan-Mar (March elevated)
    4: 1.04, 5: 1.02, 6: 0.93,   # Apr-Jun
    7: 0.82, 8: 0.89, 9: 1.07,   # Jul-Sep (July safest)
    10: 1.05, 11: 0.88, 12: 0.92, # Oct-Dec (December safe)
}

# Best/worst months for selling tails
SAFE_MONTHS = [7, 12, 11, 6]     # July, Dec, Nov, Jun — lowest tail freq
RISKY_MONTHS = [3, 9, 10, 2]     # Mar, Sep, Oct, Feb — elevated tail freq

# ── Day-of-Week Risk ────────────────────────────────────────
# Drop>2% frequency by day (0=Mon..4=Fri)
DOW_DROP2_RATE = {
    0: 0.0455,  # Monday
    1: 0.0379,  # Tuesday (calmest for >2%)
    2: 0.0386,  # Wednesday
    3: 0.0469,  # Thursday
    4: 0.0470,  # Friday (most volatile)
}

# ── Overall Baseline Stats (unconditional) ──────────────────
BASELINE_STATS = {
    "total_days": 6563,
    "mean_return": 0.0312,
    "std_dev": 1.2185,
    "worst_day": -11.98,      # 2020-03-16
    "best_day": 11.58,        # 2008-10-13
    "drop_1pct_freq": 0.1347, # ~34/year
    "drop_2pct_freq": 0.0431, # ~11/year
    "drop_3pct_freq": 0.0149, # ~4/year
    "drop_5pct_freq": 0.0030, # ~0.8/year
}

# ── Edge Rating Thresholds ──────────────────────────────────
# Market price / historical probability ratio → edge quality
def edge_rating(market_price: float, hist_prob: float) -> str:
    """Rate the edge: how much is the market overpricing the tail?"""
    if hist_prob == 0:
        return "MAXIMUM" if market_price > 0.01 else "NONE"
    ratio = market_price / hist_prob
    if ratio > 3.0:
        return "STRONG"
    elif ratio > 1.5:
        return "MODERATE"
    elif ratio > 1.0:
        return "THIN"
    return "NEGATIVE"
