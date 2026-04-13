import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split


def train_model(df: pd.DataFrame) -> dict:
    df = df.copy()
    df["ma20"]   = df["close"].rolling(20).mean()
    df["ma50"]   = df["close"].rolling(50).mean()
    df["rsi"]    = _rsi(df["close"])
    df["target"] = (df["close"].shift(-1) > df["close"]).astype(int)
    df = df.dropna()

    if len(df) < 60:
        return {"signal": "HOLD", "accuracy": 0.0}

    X = df[["ma20", "ma50", "rsi"]]
    y = df["target"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    model = RandomForestClassifier(n_estimators=100, random_state=42)
    model.fit(X_train, y_train)

    accuracy   = model.score(X_test, y_test)
    prediction = model.predict(X.iloc[-1:])[0]

    return {
        "signal":   "BUY" if prediction == 1 else "SELL",
        "accuracy": round(accuracy * 100, 2),
    }


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))
