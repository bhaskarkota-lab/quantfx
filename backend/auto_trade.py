"""
QuantFX AI — Auto Trading Bot
Runs every 15 minutes, uses the consensus strategy engine,
and is fully controllable via Telegram commands.
"""

import os
import time
import threading
from datetime import datetime, date

import pandas as pd
import MetaTrader5 as mt5
from dotenv import load_dotenv

from strategy import (
    generate_consensus_signal,
    scan_all_pairs,
    calculate_position_size,
    calculate_take_profit,
    SUPPORTED_PAIRS,
)
from ml_model import train_model
from logger import logger
from telegram_alert import send_telegram_alert
from telegram_control import get_latest_command
from database import trades_collection

load_dotenv()

# ─────────────────────────────────────────────
# Config — override via .env
# ─────────────────────────────────────────────

RISK_PERCENT       = float(os.getenv("RISK_PERCENT", 1.0))
STOP_LOSS_PIPS     = int(os.getenv("STOP_LOSS_PIPS", 20))
TAKE_PROFIT_PIPS   = int(os.getenv("TAKE_PROFIT_PIPS", 40))
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", 5))
MIN_CONFIDENCE     = int(os.getenv("MIN_CONFIDENCE", 60))
LOOP_INTERVAL_SEC  = int(os.getenv("LOOP_INTERVAL_SEC", 900))   # 15 min default
MAGIC_NUMBER       = 1001
DAILY_REPORT_HOUR  = "21"   # UTC hour to send automatic daily report


# ─────────────────────────────────────────────
# Bot state
# ─────────────────────────────────────────────

bot_enabled      = True
pause_until      = None
trades_today     = 0
last_trade_date  = None
last_report_date = None
last_signal      = "—"
last_accuracy    = 0.0


# ─────────────────────────────────────────────
# MT5 helpers
# ─────────────────────────────────────────────

def connect_mt5() -> bool:
    ok = mt5.initialize(
        login=int(os.getenv("MT5_LOGIN", 0)),
        password=os.getenv("MT5_PASSWORD", ""),
        server=os.getenv("MT5_SERVER", ""),
    )
    if not ok:
        logger.error(f"MT5 init failed: {mt5.last_error()}")
    return ok


def load_ohlcv(symbol: str, bars: int = 500) -> pd.DataFrame:
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, bars)
    if rates is not None and len(rates) > 0:
        df = pd.DataFrame(rates)
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        return df
    csv_path = f"data/{symbol}.csv"
    if os.path.exists(csv_path):
        return pd.read_csv(csv_path)
    raise RuntimeError(f"No data for {symbol}")


def get_account() -> mt5.AccountInfo | None:
    return mt5.account_info()


def get_balance() -> float:
    info = get_account()
    return info.balance if info else 10_000.0


# ─────────────────────────────────────────────
# Trade execution
# ─────────────────────────────────────────────

def place_trade(symbol: str, signal: str, confidence: int) -> bool:
    global trades_today

    if trades_today >= MAX_TRADES_PER_DAY:
        logger.info(f"Max trades/day reached ({MAX_TRADES_PER_DAY}). Skipping {symbol}.")
        return False

    if confidence < MIN_CONFIDENCE:
        logger.info(f"Confidence {confidence}% below threshold {MIN_CONFIDENCE}%. Skipping.")
        return False

    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        logger.warning(f"No tick data for {symbol}")
        return False

    is_buy  = signal == "BUY"
    price   = tick.ask if is_buy else tick.bid
    balance = get_balance()

    pos     = calculate_position_size(balance, RISK_PERCENT, STOP_LOSS_PIPS)
    levels  = calculate_take_profit(price, STOP_LOSS_PIPS,
                                    rr_ratio=TAKE_PROFIT_PIPS / max(STOP_LOSS_PIPS, 1),
                                    is_buy=is_buy)

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       pos["lot_size"],
        "type":         mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
        "price":        price,
        "sl":           levels["stop_loss"],
        "tp":           levels["take_profit"],
        "deviation":    20,
        "magic":        MAGIC_NUMBER,
        "comment":      f"QuantFX AI | conf={confidence}%",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    success = result.retcode == mt5.TRADE_RETCODE_DONE

    record = {
        "symbol":      symbol,
        "action":      signal,
        "lot":         pos["lot_size"],
        "price":       price,
        "stop_loss":   levels["stop_loss"],
        "take_profit": levels["take_profit"],
        "confidence":  confidence,
        "success":     success,
        "retcode":     result.retcode,
        "timestamp":   datetime.utcnow().isoformat(),
    }
    trades_collection.insert_one(record)

    msg = (
        f"{'✅' if success else '❌'} {signal} {symbol}\n"
        f"Price: {price} | Lot: {pos['lot_size']}\n"
        f"SL: {levels['stop_loss']} | TP: {levels['take_profit']}\n"
        f"Confidence: {confidence}% | Risk: ${pos['risk_amount']}"
    )
    logger.info(msg)
    send_telegram_alert(msg)

    if success:
        trades_today += 1

    return success


# ─────────────────────────────────────────────
# Emergency close all
# ─────────────────────────────────────────────

def close_all_positions() -> str:
    positions = mt5.positions_get()
    if not positions:
        return "No open positions to close."

    closed, failed = [], []
    for pos in positions:
        tick = mt5.symbol_info_tick(pos.symbol)
        close_type = mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY
        price = tick.bid if pos.type == 0 else tick.ask

        req = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       pos.symbol,
            "volume":       pos.volume,
            "type":         close_type,
            "position":     pos.ticket,
            "price":        price,
            "deviation":    20,
            "magic":        MAGIC_NUMBER,
            "comment":      "Emergency close",
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        r = mt5.order_send(req)
        if r.retcode == mt5.TRADE_RETCODE_DONE:
            closed.append(pos.symbol)
        else:
            failed.append(pos.symbol)

    return f"Closed: {', '.join(closed) or 'none'}\nFailed: {', '.join(failed) or 'none'}"


# ─────────────────────────────────────────────
# Telegram command handlers
# ─────────────────────────────────────────────

def handle_command(command: str):
    global bot_enabled, pause_until, trades_today

    if command == "/startbot":
        bot_enabled = True
        pause_until = None
        send_telegram_alert("▶️ Bot started. Trading resumed.")

    elif command == "/stopbot":
        bot_enabled = False
        send_telegram_alert("⏹ Bot stopped. No new trades will be placed.")

    elif command == "/pause1h":
        bot_enabled = False
        pause_until = time.time() + 3600
        send_telegram_alert("⏸ Bot paused for 1 hour. Will resume automatically.")

    elif command == "/status":
        send_telegram_alert(
            f"🤖 Bot status\n"
            f"Running: {bot_enabled}\n"
            f"Latest signal: {last_signal}\n"
            f"Model accuracy: {last_accuracy:.1f}%\n"
            f"Trades today: {trades_today}/{MAX_TRADES_PER_DAY}"
        )

    elif command == "/balance":
        acc = get_account()
        if acc:
            send_telegram_alert(
                f"💰 Account\n"
                f"Balance: ${acc.balance:,.2f}\n"
                f"Equity:  ${acc.equity:,.2f}\n"
                f"Profit:  ${acc.profit:+,.2f}\n"
                f"Free margin: ${acc.margin_free:,.2f}"
            )
        else:
            send_telegram_alert("⚠️ MT5 not connected.")

    elif command == "/trades":
        positions = mt5.positions_get()
        if positions:
            lines = ["📋 Open trades:"]
            for p in positions:
                direction = "BUY" if p.type == 0 else "SELL"
                lines.append(f"  {p.symbol} | {direction} | Lot: {p.volume} | P&L: ${p.profit:+.2f}")
            send_telegram_alert("\n".join(lines))
        else:
            send_telegram_alert("No open trades.")

    elif command == "/signal":
        send_telegram_alert(
            f"📡 Latest signal: {last_signal}\n"
            f"Model accuracy: {last_accuracy:.1f}%"
        )

    elif command == "/closeall":
        result_msg = close_all_positions()
        send_telegram_alert(f"🚨 Close all:\n{result_msg}")

    elif command == "/report":
        send_daily_report()

    elif command == "/pairs":
        # Run consensus scan and report all signals
        try:
            data = {s: load_ohlcv(s) for s in SUPPORTED_PAIRS}
            signals = scan_all_pairs(data)
            lines = ["📊 All pair signals:"]
            for s in signals:
                lines.append(f"  {s['symbol']}: {s['signal']} ({s['confidence']}%)")
            send_telegram_alert("\n".join(lines))
        except Exception as e:
            send_telegram_alert(f"⚠️ Could not scan pairs: {e}")

    elif command == "/help":
        send_telegram_alert(
            "QuantFX AI — Commands:\n\n"
            "/startbot   — Resume trading\n"
            "/stopbot    — Pause trading\n"
            "/pause1h    — Pause for 1 hour\n"
            "/status     — Bot status\n"
            "/balance    — Account balance\n"
            "/trades     — Open positions\n"
            "/signal     — Latest AI signal\n"
            "/pairs      — All pair signals\n"
            "/closeall   — Close all trades NOW\n"
            "/report     — Full performance report\n"
            "/help       — Show this menu"
        )


# ─────────────────────────────────────────────
# Daily report
# ─────────────────────────────────────────────

def send_daily_report():
    acc = get_account()
    closed = list(trades_collection.find({"success": True}))

    profits  = [t.get("profit", 0) for t in closed]
    wins     = sum(1 for p in profits if p > 0)
    losses   = len(profits) - wins
    total    = len(profits)
    win_rate = round((wins / total) * 100, 2) if total else 0
    net      = round(sum(profits), 2)
    max_dd   = round(min(profits), 2) if profits else 0
    positions = mt5.positions_get() or []

    report = (
        f"📈 QuantFX Daily Report\n"
        f"{'─' * 28}\n"
        f"Balance:      ${acc.balance:,.2f}\n"
        f"Equity:       ${acc.equity:,.2f}\n"
        f"Net profit:   ${net:+,.2f}\n"
        f"Wins:         {wins}\n"
        f"Losses:       {losses}\n"
        f"Win rate:     {win_rate}%\n"
        f"Max drawdown: ${max_dd:,.2f}\n"
        f"Open trades:  {len(positions)}\n"
        f"Bot active:   {bot_enabled}\n"
        f"Last signal:  {last_signal}\n"
        f"ML accuracy:  {last_accuracy:.1f}%"
    ) if acc else "⚠️ MT5 not connected — partial report unavailable."

    send_telegram_alert(report)


# ─────────────────────────────────────────────
# Auto-resume after pause
# ─────────────────────────────────────────────

def check_auto_resume():
    global bot_enabled, pause_until
    if not bot_enabled and pause_until and time.time() >= pause_until:
        bot_enabled = True
        pause_until = None
        send_telegram_alert("▶️ Bot resumed automatically after pause.")


# ─────────────────────────────────────────────
# Daily trade counter reset
# ─────────────────────────────────────────────

def maybe_reset_daily_counter():
    global trades_today, last_trade_date
    today = date.today().isoformat()
    if last_trade_date != today:
        trades_today    = 0
        last_trade_date = today


# ─────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────

def run_bot():
    global last_signal, last_accuracy, last_report_date, bot_enabled

    if not connect_mt5():
        send_telegram_alert("⚠️ MT5 connection failed. Running in offline mode.")

    send_telegram_alert(
        "🚀 QuantFX AI Bot started\n"
        f"Risk: {RISK_PERCENT}% | SL: {STOP_LOSS_PIPS} pips | "
        f"TP: {TAKE_PROFIT_PIPS} pips | Max trades/day: {MAX_TRADES_PER_DAY}"
    )

    while True:
        try:
            # ── Telegram commands ──────────────────
            command = get_latest_command()
            if command:
                handle_command(command)

            # ── Auto-resume from pause ─────────────
            check_auto_resume()

            # ── Daily counter reset ────────────────
            maybe_reset_daily_counter()

            # ── Automatic daily report at 21:00 UTC ─
            current_date = date.today().isoformat()
            current_hour = datetime.utcnow().strftime("%H")
            if current_hour == DAILY_REPORT_HOUR and last_report_date != current_date:
                send_daily_report()
                last_report_date = current_date

            # ── Skip trading if paused ─────────────
            if not bot_enabled:
                time.sleep(10)
                continue

            # ── Scan all pairs ─────────────────────
            data = {}
            for sym in SUPPORTED_PAIRS:
                try:
                    data[sym] = load_ohlcv(sym)
                except Exception as e:
                    logger.warning(f"Could not load {sym}: {e}")

            if not data:
                logger.warning("No market data available. Skipping cycle.")
                time.sleep(LOOP_INTERVAL_SEC)
                continue

            signals = scan_all_pairs(data)

            # ── ML confirmation on top pair ────────
            top = signals[0] if signals else None
            if top:
                try:
                    ml = train_model(data[top["symbol"]])
                    last_accuracy = ml.get("accuracy", 0)
                    last_signal   = top["signal"]
                except Exception:
                    last_accuracy = 0

            # ── Place trades ───────────────────────
            for s in signals:
                if s["signal"] == "HOLD":
                    continue
                if last_accuracy < MIN_CONFIDENCE and last_accuracy > 0:
                    logger.info(f"ML accuracy {last_accuracy}% too low. Skipping {s['symbol']}.")
                    continue
                place_trade(s["symbol"], s["signal"], s["confidence"])

        except Exception as e:
            logger.error(f"Bot loop error: {e}")
            send_telegram_alert(f"⚠️ Bot error: {e}")

        time.sleep(LOOP_INTERVAL_SEC)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    run_bot()
