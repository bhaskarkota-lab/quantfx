"""
QuantFX AI — FastAPI Backend
Wired to strategy.py (RSI, MACD, Bollinger, Trend + consensus engine)
"""

import os
import time
import logging
from contextlib import asynccontextmanager
from datetime import datetime

import pandas as pd
import MetaTrader5 as mt5
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from jose import jwt, JWTError
from passlib.context import CryptContext
from dotenv import load_dotenv

from database import trades_collection, users_collection
from strategy import (
    scan_all_pairs,
    generate_consensus_signal,
    calculate_position_size,
    calculate_take_profit,
    SUPPORTED_PAIRS,
)
from ml_model import train_model
from logger import logger

load_dotenv()

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

SECRET_KEY = os.getenv("SECRET_KEY", "change-this-in-production")
ALGORITHM = "HS256"
ACCOUNT_BALANCE = 12_450.0   # pulled from MT5 at runtime; default for calc only

logging.basicConfig(level=logging.INFO)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()


# ─────────────────────────────────────────────
# Startup / shutdown
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not mt5.initialize(
        login=int(os.getenv("MT5_LOGIN", 0)),
        password=os.getenv("MT5_PASSWORD", ""),
        server=os.getenv("MT5_SERVER", ""),
    ):
        logger.warning("MT5 initialize failed — running in offline mode")
    yield
    mt5.shutdown()


app = FastAPI(title="QuantFX AI", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def load_ohlcv(symbol: str, timeframe=mt5.TIMEFRAME_M15, bars: int = 500) -> pd.DataFrame:
    """Fetch OHLCV from MT5; fall back to CSV if MT5 is unavailable."""
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    if rates is not None and len(rates) > 0:
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.rename(columns={"open": "open", "high": "high", "low": "low",
                            "close": "close", "tick_volume": "volume"}, inplace=True)
        return df
    # Offline fallback — load from local CSV named e.g. "EURUSD.csv"
    csv_path = f"data/{symbol}.csv"
    if os.path.exists(csv_path):
        return pd.read_csv(csv_path)
    raise HTTPException(status_code=503, detail=f"No data available for {symbol}")


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> str:
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return email
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_account_balance() -> float:
    info = mt5.account_info()
    return info.balance if info else ACCOUNT_BALANCE


# ─────────────────────────────────────────────
# Request / response models
# ─────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str

class TradeRequest(BaseModel):
    symbol: str
    action: str          # "BUY" or "SELL"
    lot: float
    stop_loss_pips: int = 20
    take_profit_pips: int = 40

class RiskRequest(BaseModel):
    account_balance: float
    risk_percent: float
    stop_loss_pips: int
    take_profit_pips: int


# ─────────────────────────────────────────────
# Auth endpoints
# ─────────────────────────────────────────────

@app.post("/register", tags=["auth"])
def register(req: LoginRequest):
    if users_collection.find_one({"email": req.email}):
        raise HTTPException(status_code=400, detail="User already exists")
    users_collection.insert_one({
        "email": req.email,
        "password": pwd_context.hash(req.password),
        "created_at": datetime.utcnow().isoformat(),
    })
    return {"message": "Account created. Please log in."}


@app.post("/login", tags=["auth"])
def login(req: LoginRequest):
    user = users_collection.find_one({"email": req.email})
    if not user or not pwd_context.verify(req.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = jwt.encode({"sub": req.email}, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": token, "token_type": "bearer"}


# ─────────────────────────────────────────────
# Market data
# ─────────────────────────────────────────────

@app.get("/price/{symbol}", tags=["market"])
def get_price(symbol: str):
    tick = mt5.symbol_info_tick(symbol.upper())
    if not tick:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    return {"symbol": symbol.upper(), "bid": tick.bid, "ask": tick.ask,
            "spread": round(tick.ask - tick.bid, 5), "time": tick.time}


# ─────────────────────────────────────────────
# Signal endpoints
# ─────────────────────────────────────────────

@app.get("/signals", tags=["signals"])
def get_all_signals():
    """Consensus signal for all supported pairs."""
    data = {}
    for sym in SUPPORTED_PAIRS:
        try:
            data[sym] = load_ohlcv(sym)
        except HTTPException:
            pass
    if not data:
        raise HTTPException(status_code=503, detail="No market data available")
    results = scan_all_pairs(data)
    return {"timestamp": datetime.utcnow().isoformat(), "signals": results}


@app.get("/signals/{symbol}", tags=["signals"])
def get_signal(symbol: str):
    """Consensus signal for a single pair with full strategy breakdown."""
    sym = symbol.upper()
    df = load_ohlcv(sym)
    result = generate_consensus_signal(df)
    return {"symbol": sym, "timestamp": datetime.utcnow().isoformat(), **result}


@app.get("/predict", tags=["signals"])
def predict_ml():
    """Random-forest ML prediction on EUR/USD (legacy endpoint)."""
    df = load_ohlcv("EURUSD")
    result = train_model(df)
    return result


# ─────────────────────────────────────────────
# Backtesting
# ─────────────────────────────────────────────

@app.get("/backtest/{symbol}", tags=["backtest"])
def backtest(symbol: str, strategy: str = "trend"):
    """
    Walk-forward backtest on historical data.
    strategy options: trend | rsi | macd | bollinger | consensus
    """
    sym = symbol.upper()
    df = load_ohlcv(sym, bars=1000)

    from strategy import (
        signal_trend_following, signal_rsi_reversal,
        signal_macd_crossover, signal_bollinger_squeeze,
        generate_consensus_signal,
    )

    fn_map = {
        "trend": signal_trend_following,
        "rsi": signal_rsi_reversal,
        "macd": signal_macd_crossover,
        "bollinger": signal_bollinger_squeeze,
        "consensus": generate_consensus_signal,
    }
    fn = fn_map.get(strategy, signal_trend_following)

    wins = losses = 0
    profits = []
    pip_size = 0.0001 if "JPY" not in sym else 0.01

    for i in range(60, len(df) - 1):
        window = df.iloc[:i]
        try:
            result = fn(window)
        except Exception:
            continue

        sig = result["signal"]
        if sig == "HOLD":
            continue

        entry = df.iloc[i]["close"]
        exit_price = df.iloc[i + 1]["close"]
        diff = exit_price - entry if sig == "BUY" else entry - exit_price
        pips = diff / pip_size

        profits.append(round(pips, 1))
        if pips > 0:
            wins += 1
        else:
            losses += 1

    total = wins + losses
    win_rate = round((wins / total) * 100, 2) if total else 0
    avg_profit = round(sum(profits) / len(profits), 2) if profits else 0
    max_dd = round(min(profits), 2) if profits else 0

    return {
        "symbol": sym,
        "strategy": strategy,
        "wins": wins,
        "losses": losses,
        "total_trades": total,
        "win_rate": win_rate,
        "avg_profit_pips": avg_profit,
        "max_drawdown_pips": max_dd,
    }


# ─────────────────────────────────────────────
# Risk management
# ─────────────────────────────────────────────

@app.post("/risk/calculate", tags=["risk"])
def calculate_risk(req: RiskRequest):
    pos = calculate_position_size(
        req.account_balance, req.risk_percent, req.stop_loss_pips
    )
    rr = round(req.take_profit_pips / req.stop_loss_pips, 2) if req.stop_loss_pips else 0
    return {**pos, "rr_ratio": rr, "take_profit_pips": req.take_profit_pips}


# ─────────────────────────────────────────────
# Trade execution
# ─────────────────────────────────────────────

@app.post("/trade", tags=["trading"])
def place_trade(req: TradeRequest, email: str = Depends(verify_token)):
    sym = req.symbol.upper()
    action = req.action.upper()
    if action not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="action must be BUY or SELL")

    tick = mt5.symbol_info_tick(sym)
    if not tick:
        raise HTTPException(status_code=404, detail=f"Symbol {sym} not found")

    is_buy = action == "BUY"
    price = tick.ask if is_buy else tick.bid
    order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL

    levels = calculate_take_profit(price, req.stop_loss_pips,
                                   rr_ratio=req.take_profit_pips / max(req.stop_loss_pips, 1),
                                   is_buy=is_buy)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": sym,
        "volume": req.lot,
        "type": order_type,
        "price": price,
        "sl": levels["stop_loss"],
        "tp": levels["take_profit"],
        "deviation": 20,
        "magic": 1001,
        "comment": "QuantFX AI Bot",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    success = result.retcode == mt5.TRADE_RETCODE_DONE

    record = {
        "symbol": sym,
        "action": action,
        "lot": req.lot,
        "price": price,
        "stop_loss": levels["stop_loss"],
        "take_profit": levels["take_profit"],
        "success": success,
        "retcode": result.retcode,
        "user": email,
        "timestamp": datetime.utcnow().isoformat(),
    }
    trades_collection.insert_one(record)

    msg = f"{action} {sym} @ {price} | lot={req.lot} | SL={levels['stop_loss']} TP={levels['take_profit']}"
    logger.info(msg)

    if not success:
        raise HTTPException(status_code=400, detail=f"Trade failed: retcode {result.retcode}")

    return {"success": True, "price": price, **levels}


# ─────────────────────────────────────────────
# Trade history & analytics
# ─────────────────────────────────────────────

@app.get("/trades", tags=["analytics"])
def get_trades(email: str = Depends(verify_token)):
    trades = list(trades_collection.find({"user": email}, {"_id": 0})
                  .sort("timestamp", -1).limit(100))
    return trades


@app.get("/analytics", tags=["analytics"])
def get_analytics(email: str = Depends(verify_token)):
    closed = list(trades_collection.find({"user": email, "success": True}, {"_id": 0}))
    profits = [t.get("profit", 0) for t in closed]

    equity_curve, drawdowns = [], []
    running = max_eq = 0
    for p in profits:
        running += p
        equity_curve.append(round(running, 2))
        max_eq = max(max_eq, running)
        drawdowns.append(round(running - max_eq, 2))

    wins = sum(1 for p in profits if p > 0)
    losses = len(profits) - wins
    total = len(profits)

    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round((wins / total) * 100, 2) if total else 0,
        "total_profit": round(sum(profits), 2),
        "average_profit": round(sum(profits) / total, 2) if total else 0,
        "best_trade": max(profits) if profits else 0,
        "worst_trade": min(profits) if profits else 0,
        "max_drawdown": min(drawdowns) if drawdowns else 0,
        "equity_curve": equity_curve,
        "drawdowns": drawdowns,
    }


# ─────────────────────────────────────────────
# Account info
# ─────────────────────────────────────────────

@app.get("/account", tags=["trading"])
def get_account(email: str = Depends(verify_token)):
    info = mt5.account_info()
    if not info:
        raise HTTPException(status_code=503, detail="MT5 not connected")
    return {
        "balance": info.balance,
        "equity": info.equity,
        "profit": round(info.profit, 2),
        "margin": info.margin,
        "free_margin": info.margin_free,
        "currency": info.currency,
        "leverage": info.leverage,
    }


@app.get("/positions", tags=["trading"])
def get_open_positions(email: str = Depends(verify_token)):
    positions = mt5.positions_get()
    if positions is None:
        return []
    return [
        {
            "ticket": p.ticket,
            "symbol": p.symbol,
            "action": "BUY" if p.type == 0 else "SELL",
            "volume": p.volume,
            "open_price": p.price_open,
            "current_price": p.price_current,
            "profit": round(p.profit, 2),
            "sl": p.sl,
            "tp": p.tp,
        }
        for p in positions
    ]


@app.post("/positions/close-all", tags=["trading"])
def close_all_positions(email: str = Depends(verify_token)):
    positions = mt5.positions_get()
    if not positions:
        return {"message": "No open positions"}

    closed, failed = [], []
    for pos in positions:
        tick = mt5.symbol_info_tick(pos.symbol)
        close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
        price = tick.bid if pos.type == 0 else tick.ask

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": pos.ticket,
            "price": price,
            "deviation": 20,
            "magic": 1001,
            "comment": "Emergency close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(req)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            closed.append(pos.ticket)
        else:
            failed.append(pos.ticket)

    logger.info(f"close-all by {email}: closed={closed} failed={failed}")
    return {"closed": closed, "failed": failed}


# ─────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────

@app.get("/health", tags=["system"])
def health_check():
    mt5_ok = mt5.terminal_info() is not None
    return {
        "status": "running",
        "bot": "QuantFX AI v2",
        "mt5_connected": mt5_ok,
        "database": "connected",
        "trading": "active" if mt5_ok else "offline",
        "timestamp": datetime.utcnow().isoformat(),
        "supported_pairs": SUPPORTED_PAIRS,
    }


# ─────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
