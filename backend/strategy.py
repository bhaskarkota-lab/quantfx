import pandas as pd
import numpy as np


# ─────────────────────────────────────────────
# Indicator calculations
# ─────────────────────────────────────────────

def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calculate_macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame({
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram,
    })


def calculate_bollinger_bands(
    series: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> pd.DataFrame:
    sma = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = sma + std_dev * std
    lower = sma - std_dev * std
    bandwidth = (upper - lower) / sma
    percent_b = (series - lower) / (upper - lower)
    return pd.DataFrame({
        "upper": upper,
        "middle": sma,
        "lower": lower,
        "bandwidth": bandwidth,
        "percent_b": percent_b,
    })


def calculate_moving_averages(series: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({
        "ma20": series.rolling(20).mean(),
        "ma50": series.rolling(50).mean(),
        "ma200": series.rolling(200).mean(),
        "ema20": series.ewm(span=20, adjust=False).mean(),
    })


# ─────────────────────────────────────────────
# Individual strategy signal generators
# ─────────────────────────────────────────────

def signal_trend_following(df: pd.DataFrame) -> dict:
    """MA20 / MA50 crossover with EMA confirmation."""
    mas = calculate_moving_averages(df["close"])
    latest = mas.iloc[-1]
    prev = mas.iloc[-2]

    bullish_cross = prev["ma20"] <= prev["ma50"] and latest["ma20"] > latest["ma50"]
    bearish_cross = prev["ma20"] >= prev["ma50"] and latest["ma20"] < latest["ma50"]
    above_200 = df["close"].iloc[-1] > latest["ma200"]

    if bullish_cross and above_200:
        signal, confidence = "BUY", 80
    elif bullish_cross:
        signal, confidence = "BUY", 62
    elif bearish_cross and not above_200:
        signal, confidence = "SELL", 80
    elif bearish_cross:
        signal, confidence = "SELL", 62
    elif latest["ma20"] > latest["ma50"]:
        signal, confidence = "BUY", 55
    elif latest["ma20"] < latest["ma50"]:
        signal, confidence = "SELL", 55
    else:
        signal, confidence = "HOLD", 40

    return {
        "strategy": "trend_following",
        "signal": signal,
        "confidence": confidence,
        "details": {
            "ma20": round(latest["ma20"], 5),
            "ma50": round(latest["ma50"], 5),
            "ma200": round(latest["ma200"], 5),
            "above_200ma": above_200,
        },
    }


def signal_rsi_reversal(df: pd.DataFrame, period: int = 14) -> dict:
    """RSI mean-reversion: buy oversold, sell overbought."""
    rsi = calculate_rsi(df["close"], period)
    latest = rsi.iloc[-1]
    prev = rsi.iloc[-2]

    if latest < 30:
        signal = "BUY"
        confidence = int(80 - latest)          # deeper = higher confidence
    elif latest < 40 and prev >= 40:
        signal = "BUY"
        confidence = 60
    elif latest > 70:
        signal = "SELL"
        confidence = int(latest - 20)
    elif latest > 60 and prev <= 60:
        signal = "SELL"
        confidence = 60
    else:
        signal = "HOLD"
        confidence = 40

    return {
        "strategy": "rsi_reversal",
        "signal": signal,
        "confidence": min(confidence, 95),
        "details": {
            "rsi": round(latest, 2),
            "oversold": latest < 30,
            "overbought": latest > 70,
        },
    }


def signal_macd_crossover(df: pd.DataFrame) -> dict:
    """MACD line / signal line crossover with histogram momentum."""
    macd_df = calculate_macd(df["close"])
    latest = macd_df.iloc[-1]
    prev = macd_df.iloc[-2]

    bullish_cross = prev["macd"] <= prev["signal"] and latest["macd"] > latest["signal"]
    bearish_cross = prev["macd"] >= prev["signal"] and latest["macd"] < latest["signal"]
    momentum_growing = abs(latest["histogram"]) > abs(prev["histogram"])

    if bullish_cross:
        signal = "BUY"
        confidence = 75 if momentum_growing else 60
    elif bearish_cross:
        signal = "SELL"
        confidence = 75 if momentum_growing else 60
    elif latest["macd"] > latest["signal"] and latest["histogram"] > 0:
        signal = "BUY"
        confidence = 52
    elif latest["macd"] < latest["signal"] and latest["histogram"] < 0:
        signal = "SELL"
        confidence = 52
    else:
        signal = "HOLD"
        confidence = 40

    return {
        "strategy": "macd_crossover",
        "signal": signal,
        "confidence": confidence,
        "details": {
            "macd": round(latest["macd"], 6),
            "signal_line": round(latest["signal"], 6),
            "histogram": round(latest["histogram"], 6),
            "momentum_growing": momentum_growing,
        },
    }


def signal_bollinger_squeeze(df: pd.DataFrame) -> dict:
    """Bollinger Band squeeze breakout + %B position."""
    bb = calculate_bollinger_bands(df["close"])
    latest = bb.iloc[-1]
    prev_20 = bb.tail(20)
    close = df["close"].iloc[-1]

    squeeze = latest["bandwidth"] < prev_20["bandwidth"].quantile(0.25)
    breakout_up = close > latest["upper"]
    breakout_down = close < latest["lower"]
    percent_b = latest["percent_b"]

    if breakout_up and squeeze:
        signal, confidence = "BUY", 78
    elif breakout_up:
        signal, confidence = "BUY", 62
    elif breakout_down and squeeze:
        signal, confidence = "SELL", 78
    elif breakout_down:
        signal, confidence = "SELL", 62
    elif percent_b < 0.2:
        signal, confidence = "BUY", 52
    elif percent_b > 0.8:
        signal, confidence = "SELL", 52
    else:
        signal, confidence = "HOLD", 40

    return {
        "strategy": "bollinger_squeeze",
        "signal": signal,
        "confidence": confidence,
        "details": {
            "upper": round(latest["upper"], 5),
            "middle": round(latest["middle"], 5),
            "lower": round(latest["lower"], 5),
            "percent_b": round(percent_b, 3),
            "squeeze": squeeze,
        },
    }


# ─────────────────────────────────────────────
# Multi-strategy consensus engine
# ─────────────────────────────────────────────

STRATEGY_WEIGHTS = {
    "trend_following": 1.0,
    "rsi_reversal": 0.8,
    "macd_crossover": 0.9,
    "bollinger_squeeze": 0.7,
}


def generate_consensus_signal(df: pd.DataFrame) -> dict:
    """
    Run all four strategies and combine their signals using
    weighted voting. Returns a final signal with overall confidence.
    """
    results = [
        signal_trend_following(df),
        signal_rsi_reversal(df),
        signal_macd_crossover(df),
        signal_bollinger_squeeze(df),
    ]

    buy_score = sell_score = 0.0

    for r in results:
        weight = STRATEGY_WEIGHTS[r["strategy"]]
        weighted_conf = r["confidence"] * weight
        if r["signal"] == "BUY":
            buy_score += weighted_conf
        elif r["signal"] == "SELL":
            sell_score += weighted_conf

    total = buy_score + sell_score or 1
    if buy_score > sell_score and buy_score / total > 0.55:
        final_signal = "BUY"
        final_confidence = int(buy_score / total * 100)
    elif sell_score > buy_score and sell_score / total > 0.55:
        final_signal = "SELL"
        final_confidence = int(sell_score / total * 100)
    else:
        final_signal = "HOLD"
        final_confidence = 40

    return {
        "signal": final_signal,
        "confidence": min(final_confidence, 95),
        "buy_score": round(buy_score, 1),
        "sell_score": round(sell_score, 1),
        "strategies": results,
    }


# ─────────────────────────────────────────────
# Multi-pair scanner
# ─────────────────────────────────────────────

SUPPORTED_PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY",
    "AUDUSD", "EURGBP", "USDCAD",
]


def scan_all_pairs(data: dict[str, pd.DataFrame]) -> list[dict]:
    """
    Run the consensus engine across every pair.
    `data` is a dict mapping symbol -> DataFrame with a 'close' column.
    Returns a sorted list: BUY/SELL signals first, HOLD last.
    """
    output = []
    for symbol in SUPPORTED_PAIRS:
        df = data.get(symbol)
        if df is None or len(df) < 50:
            continue
        result = generate_consensus_signal(df)
        output.append({"symbol": symbol, **result})

    priority = {"BUY": 0, "SELL": 1, "HOLD": 2}
    return sorted(output, key=lambda x: (priority[x["signal"]], -x["confidence"]))


# ─────────────────────────────────────────────
# Risk management helpers
# ─────────────────────────────────────────────

def calculate_position_size(
    account_balance: float,
    risk_percent: float,
    stop_loss_pips: int,
    pip_value: float = 10.0,
) -> dict:
    risk_amount = account_balance * (risk_percent / 100)
    lot_size = risk_amount / (stop_loss_pips * pip_value)
    return {
        "lot_size": round(lot_size, 2),
        "risk_amount": round(risk_amount, 2),
        "risk_percent": risk_percent,
    }


def calculate_take_profit(
    entry: float,
    stop_loss_pips: int,
    rr_ratio: float = 2.0,
    is_buy: bool = True,
    pip_size: float = 0.0001,
) -> dict:
    sl_distance = stop_loss_pips * pip_size
    tp_distance = sl_distance * rr_ratio
    if is_buy:
        sl_price = entry - sl_distance
        tp_price = entry + tp_distance
    else:
        sl_price = entry + sl_distance
        tp_price = entry - tp_distance
    return {
        "entry": round(entry, 5),
        "stop_loss": round(sl_price, 5),
        "take_profit": round(tp_price, 5),
        "rr_ratio": rr_ratio,
    }


# ─────────────────────────────────────────────
# Quick smoke test
# ─────────────────────────────────────────────

if __name__ == "__main__":
    np.random.seed(42)
    n = 300
    close = pd.Series(
        1.08 + np.cumsum(np.random.randn(n) * 0.0005),
        name="close",
    )
    df_test = pd.DataFrame({"close": close})

    print("=== Individual strategies ===")
    for fn in [signal_trend_following, signal_rsi_reversal,
               signal_macd_crossover, signal_bollinger_squeeze]:
        r = fn(df_test)
        print(f"  {r['strategy']:22s}  {r['signal']:4s}  conf={r['confidence']}%")

    print("\n=== Consensus signal ===")
    consensus = generate_consensus_signal(df_test)
    print(f"  Final: {consensus['signal']}  confidence={consensus['confidence']}%")
    print(f"  Buy score: {consensus['buy_score']}  Sell score: {consensus['sell_score']}")

    print("\n=== Position sizing ===")
    pos = calculate_position_size(12450, 1.0, 20)
    print(f"  Lot: {pos['lot_size']}  Risk: ${pos['risk_amount']}")

    print("\n=== TP/SL levels ===")
    levels = calculate_take_profit(1.08750, 20, rr_ratio=2.0, is_buy=True)
    print(f"  Entry: {levels['entry']}  SL: {levels['stop_loss']}  TP: {levels['take_profit']}")
