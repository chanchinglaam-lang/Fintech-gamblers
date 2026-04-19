#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spot-style rules bot for Roostoo: STO, a multi-asset group (ZEC, BTC, ETH, TAO, WLD, NEAR),
and PENGU with staged timing. Market data from Binance public klines; orders via Roostoo REST.

Dependencies: pip install requests pytz python-binance
Run: python Team53code.py
"""

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta

import pytz
import requests
from binance.client import Client

# --- Roostoo API (RST-API-KEY / MSG-SIGNATURE on mock-api.roostoo.com) ---
API_KEY = "v8JX4znMnQ83o7SYwPNElnxXPSAOkKE0iLHqvorTfNdaXl22KT516oGke0AJggJp"
SECRET_KEY = "hWnYKvTthqd2Q9W6AIQjCejHrmG9YbZlMZl3uSdkAaSnpubAgE1qVUUt4sF3zYEm"
BASE_URL = "https://mock-api.roostoo.com"

# Strategy parameters (instruction sheet: x1,y1,z1 for STO; x2,y2,z2 for group 2; x3 for PENGU timing)
X1, Y1, Z1 = 2.0, 1.0, 0.5
X2, Y2, Z2 = 2.0, 1.0, 0.5

# Group 2: minimum trailing weekly range (high-low)/low, expressed as a percentage
GROUP2_WEEK_RANGE_MIN_PCT = 60.0
# Group 2 exits: same 1h momentum test as STO (last closed hour vs z2), but only after a short minimum hold;
# also a hard maximum hold so positions do not stay open indefinitely.
GROUP2_MIN_SECONDS_BEFORE_1H_EXIT = 60
GROUP2_MAX_HOLD_SECONDS = 94356

# PENGU (instruction: x3 plus random delay; no competing legs active): base quiet period after any STO or group-2 fill or exit
X3_SECONDS = 9 * 3600
X3_RANDOM_EXTRA_MAX = 2 * 3600
# After a completed PENGU round-trip, wait again before the next PENGU entry (randomized)
PENGU_NEXT_ROUND_MIN_SEC = 300
PENGU_NEXT_ROUND_MAX_SEC = 7200

# Binance client: public endpoints only (klines and ticker)
binance = Client()

STO_SYMBOL = "STOUSDT"
GROUP2_SYMBOLS = ("ZECUSDT", "BTCUSDT", "ETHUSDT", "TAOUSDT", "WLDUSDT", "NEARUSDT")
ZEC_SYMBOL = "ZECUSDT"
PENGU_SYMBOL = "PENGUUSDT"

# Instruction label "WILD" mapped to WLDUSDT (Worldcoin) on this venue’s symbol list
EST = pytz.timezone("America/New_York")


def _ts_ms():
    return str(int(time.time() * 1000))


def _signed_headers(payload):
    payload = dict(payload)
    payload["timestamp"] = _ts_ms()
    keys = sorted(payload.keys())
    total_params = "&".join(f"{k}={payload[k]}" for k in keys)
    sig = hmac.new(
        SECRET_KEY.encode("utf-8"),
        total_params.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "RST-API-KEY": API_KEY,
        "MSG-SIGNATURE": sig,
    }, payload, total_params


def get_balance():
    url = f"{BASE_URL}/v3/balance"
    headers, payload, _ = _signed_headers({})
    try:
        res = requests.get(url, headers=headers, params=payload, timeout=30)
        res.raise_for_status()
        return res.json()
    except requests.exceptions.RequestException as e:
        print(f"Error getting balance: {e}")
        if e.response is not None:
            print(e.response.text)
        return None


def usd_free(balance_json):
    if not balance_json or not balance_json.get("Success"):
        return 0.0
    spot = balance_json.get("SpotWallet", {})
    usd = spot.get("USD", {})
    return float(usd.get("Free", 0.0))


def send_roostoo_order(symbol, side, quantity):
    if quantity <= 0:
        return None
    pair = symbol.replace("USDT", "/USD")
    url = f"{BASE_URL}/v3/place_order"
    payload = {
        "pair": pair,
        "side": side.upper(),
        "type": "MARKET",
        "quantity": str(round(quantity, 8)),
    }
    headers, _, total_params = _signed_headers(payload)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        res = requests.post(url, headers=headers, data=total_params, timeout=30)
        res.raise_for_status()
        out = res.json()
        print(out)
        print(f"[ORDER] {side} {quantity} {pair}")
        return out
    except Exception as e:
        print(f"Order error: {e}")
        if getattr(e, "response", None) is not None:
            print(e.response.text)
        return None


def _last_closed_candle_pct(symbol, interval):
    """Percentage change (close - open) / open of the most recently completed bar."""
    kl = binance.get_klines(symbol=symbol, interval=interval, limit=3)
    if len(kl) < 2:
        return None
    # Index -2: last fully closed bar (exclude the still-forming bar at [-1])
    o, c = float(kl[-2][1]), float(kl[-2][4])
    if o == 0:
        return None
    return (c - o) / o * 100.0


def weekly_range_and_price(symbol, days=7):
    """
    Rolling window of daily bars: session high/low, last daily close as reference price,
    and range percent versus the window low: (high - low) / low * 100.
    """
    end = datetime.utcnow()
    start = end - timedelta(days=days + 2)
    s_ms = int(start.timestamp() * 1000)
    e_ms = int(end.timestamp() * 1000)
    kl = binance.get_klines(symbol=symbol, interval="1d", startTime=s_ms, endTime=e_ms)
    if not kl:
        return None
    highs = [float(x[2]) for x in kl]
    lows = [float(x[3]) for x in kl]
    last_close = float(kl[-1][4])
    wh, wl = max(highs), min(lows)
    if wl <= 0:
        return None
    range_pct = (wh - wl) / wl * 100.0
    return {"high": wh, "low": wl, "range_pct": range_pct, "price": last_close}


def sto_entry_ok():
    w = weekly_range_and_price(STO_SYMBOL)
    if not w:
        return False, None
    if w["range_pct"] < 200.0:
        return False, w
    if not (w["price"] < w["low"] + (w["high"] - w["low"]) / 4.0):
        return False, w
    h1 = _last_closed_candle_pct(STO_SYMBOL, "1h")
    m5 = _last_closed_candle_pct(STO_SYMBOL, "5m")
    if h1 is None or m5 is None:
        return False, w
    if h1 <= X1 or m5 <= Y1:
        return False, w
    return True, w


def group2_entry_ok(symbol):
    w = weekly_range_and_price(symbol)
    if not w:
        return False, None
    if w["range_pct"] < GROUP2_WEEK_RANGE_MIN_PCT:
        return False, w
    h1 = _last_closed_candle_pct(symbol, "1h")
    m5 = _last_closed_candle_pct(symbol, "5m")
    if h1 is None or m5 is None:
        return False, w
    if h1 <= X2 or m5 <= Y2:
        return False, w
    return True, w


def should_exit_by_1h(symbol, z_threshold):
    h1 = _last_closed_candle_pct(symbol, "1h")
    if h1 is None:
        return False
    return h1 < z_threshold


def market_price(symbol):
    t = binance.get_symbol_ticker(symbol=symbol)
    return float(t["price"])


def _has_open_sto_or_group2(positions):
    """True if STO or any group-2 symbol has a non-zero tracked position."""
    if STO_SYMBOL in positions:
        return True
    return any(s in positions for s in GROUP2_SYMBOLS)


def _schedule_pengu_after_other_coin(rng, now=None):
    """Earliest time PENGU may be considered again after activity on STO or group-2 (x3 + uniform random)."""
    now = now or time.time()
    return now + X3_SECONDS + rng.uniform(0.0, float(X3_RANDOM_EXTRA_MAX))


def main():
    rng = secrets.SystemRandom()
    positions = {}
    entry_time = {}
    pengu_next_buy_after = _schedule_pengu_after_other_coin(rng, time.time())
    pengu_sell_deadline = None
    print(
        f"PENGU entry window opens at (UTC) "
        f"{datetime.utcfromtimestamp(pengu_next_buy_after).isoformat()}Z "
        f"after quiet period from start."
    )

    poll = 60
    while True:
        now = time.time()
        bal = get_balance()
        usd = usd_free(bal)
        print(f"\n[{datetime.now(EST)}] USD free: {usd:.2f}")

        # Exits: STO and group 2 use the one-hour bar vs z; group 2 also enforces max hold
        for sym in list(positions.keys()):
            qty = positions[sym]
            if qty <= 0:
                del positions[sym]
                entry_time.pop(sym, None)
                continue
            if sym == STO_SYMBOL:
                z = Z1
                if should_exit_by_1h(sym, z):
                    send_roostoo_order(sym, "SELL", qty)
                    positions.pop(sym, None)
                    entry_time.pop(sym, None)
                    pengu_next_buy_after = _schedule_pengu_after_other_coin(rng, now)
            elif sym in GROUP2_SYMBOLS:
                z = Z2
                held = now - entry_time.get(sym, now)
                if held >= GROUP2_MAX_HOLD_SECONDS:
                    send_roostoo_order(sym, "SELL", qty)
                    positions.pop(sym, None)
                    entry_time.pop(sym, None)
                    pengu_next_buy_after = _schedule_pengu_after_other_coin(rng, now)
                elif (
                    held >= GROUP2_MIN_SECONDS_BEFORE_1H_EXIT
                    and should_exit_by_1h(sym, z)
                ):
                    send_roostoo_order(sym, "SELL", qty)
                    positions.pop(sym, None)
                    entry_time.pop(sym, None)
                    pengu_next_buy_after = _schedule_pengu_after_other_coin(rng, now)

        # PENGU: exit at scheduled time after entry (randomized hold)
        if (
            PENGU_SYMBOL in positions
            and pengu_sell_deadline
            and now >= pengu_sell_deadline
        ):
            send_roostoo_order(PENGU_SYMBOL, "SELL", positions[PENGU_SYMBOL])
            positions.pop(PENGU_SYMBOL, None)
            pengu_sell_deadline = None
            pengu_next_buy_after = now + rng.uniform(
                PENGU_NEXT_ROUND_MIN_SEC, PENGU_NEXT_ROUND_MAX_SEC
            )

        # STO: up to half of free USD when entry rules pass
        if STO_SYMBOL not in positions:
            ok, _ = sto_entry_ok()
            if ok and usd > 1.0:
                px = market_price(STO_SYMBOL)
                q = (usd * 0.50) / px * 0.99
                send_roostoo_order(STO_SYMBOL, "BUY", q)
                positions[STO_SYMBOL] = positions.get(STO_SYMBOL, 0) + q
                pengu_next_buy_after = _schedule_pengu_after_other_coin(rng, now)
                bal = get_balance()
                usd = usd_free(bal)

        # Group 2: up to five percent of free USD per symbol when entry rules pass
        for sym in GROUP2_SYMBOLS:
            if sym in positions:
                continue
            ok, _ = group2_entry_ok(sym)
            if ok and usd > 1.0:
                px = market_price(sym)
                q = (usd * 0.05) / px * 0.99
                send_roostoo_order(sym, "BUY", q)
                positions[sym] = positions.get(sym, 0) + q
                entry_time.setdefault(sym, now)
                pengu_next_buy_after = _schedule_pengu_after_other_coin(rng, now)
                bal = get_balance()
                usd = usd_free(bal)

        # PENGU: require no open STO or group-2 position, quiet period elapsed, and free balance
        if (
            PENGU_SYMBOL not in positions
            and not _has_open_sto_or_group2(positions)
            and now >= pengu_next_buy_after
            and usd > 1.0
        ):
            px = market_price(PENGU_SYMBOL)
            q = (usd * 0.05) / px * 0.99
            send_roostoo_order(PENGU_SYMBOL, "BUY", q)
            positions[PENGU_SYMBOL] = positions.get(PENGU_SYMBOL, 0) + q
            pengu_sell_deadline = now + rng.uniform(300.0, 7200.0)
            print(f"PENGU: randomized exit in {pengu_sell_deadline - now:.0f} s")

        time.sleep(poll)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped.")
