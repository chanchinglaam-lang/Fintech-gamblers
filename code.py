import time
import hmac
import hashlib
import requests
from datetime import datetime, timedelta
import pytz
from binance.client import Client
import os

# ==========================================================
# 1. API 配置區
# ==========================================================
API_KEY = os.getenv("ROOSTOO_API_KEY", "kjpsVgSVZZn5TYqKTFf3CFD1gFzMD3rvLMmN8M40NeLNeJJuSiykgNqHS09fB089")
SECRET_KEY = os.getenv("ROOSTOO_SECRET", "vx6EjZWBy3Ssu97QqNY7hZaQpiVk8aCBDeIGoyP2hlgtWhAkFdDL3cNEAp8j6lhC")
BASE_URL = "https://mock-api.roostoo.com"

# Binance API (僅用於數據抓取)
binance_client = Client()

# 幣種列表
roostoo_symbols = [
    "BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "ZECUSDT", "DOGEUSDT", 
    "BNBUSDT", "PEPEUSDT", "ASTERUSDT", "TRXUSDT", "TAOUSDT", "SUIUSDT", 
    "ADAUSDT", "LINKUSDT", "FETUSDT", "AVAXUSDT", "PAXGUSDT", "TRUMPUSDT", 
    "NEARUSDT", "LTCUSDT", "BONKUSDT", "XPLUSDT", "PENGUUSDT", "WLDUSDT", 
    "ENAUSDT", "UNIUSDT", "DOTUSDT", "FILUSDT", "VIRTUALUSDT", "HBARUSDT", 
    "AAVEUSDT", "PUMPUSDT", "WIFUSDT", "WLFIUSDT", "ICPUSDT", "XLMUSDT", 
    "SHIBUSDT", "CAKEUSDT", "APTUSDT", "TONUSDT", "STOUSDT", "ZENUSDT", 
    "ARBUSDT", "EIGENUSDT", "POLUSDT", "FLOKIUSDT", "CRVUSDT", "ONDOUSDT", 
    "SEIUSDT", "CFXUSDT", "SUSDT", "BIOUSDT", "PLUMEUSDT", "LINEAUSDT", 
    "PENDLEUSDT", "HEMIUSDT", "FORMUSDT", "OMNIUSDT", "LISTAUSDT", "MIRAUSDT", 
    "AVNTUSDT", "1000CHEEMSUSDT", "SOMIUSDT", "TUTUSDT", "OPENUSDT", "EDENUSDT"
]

# ==========================================================
# 2. Roostoo API 功能函數 (完全比照 testing.py)
# ==========================================================
def _get_timestamp():
    """Return a 13-digit millisecond timestamp as string."""
    return str(int(time.time() * 1000))


def _get_signed_headers(payload: dict = {}):
    """
    Generate signed headers and totalParams for RCL_TopLevelCheck endpoints.
    """
    payload['timestamp'] = _get_timestamp()
    sorted_keys = sorted(payload.keys())
    total_params = "&".join(f"{k}={payload[k]}" for k in sorted_keys)

    signature = hmac.new(
        SECRET_KEY.encode('utf-8'),
        total_params.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    headers = {
        'RST-API-KEY': API_KEY,
        'MSG-SIGNATURE': signature
    }

    return headers, payload, total_params


def get_balance():
    """Get wallet balances (RCL_TopLevelCheck). - Exactly same as testing.py"""
    url = f"{BASE_URL}/v3/balance"
    headers, payload, _ = _get_signed_headers({})
    try:
        res = requests.get(url, headers=headers, params=payload)
        res.raise_for_status()
        return res.json()
    except requests.exceptions.RequestException as e:
        print(f"Error getting balance: {e}")
        print(f"Response text: {e.response.text if e.response else 'N/A'}")
        return None

def send_roostoo_order(symbol, side, quantity):
    """向 Roostoo 發送訂單 (邏輯同步 testing.py 的 place_order)"""
    if quantity <= 0: return
    
    pair = symbol.replace("USDT", "/USD")
    url = f"{BASE_URL}/v3/place_order"
    
    payload = {
        "pair": pair,
        "side": side.upper(),
        "type": "MARKET",
        "quantity": str(round(quantity, 6))
    }
    
    headers, _, total_params = _get_signed_headers(payload)
    headers['Content-Type'] = 'application/x-www-form-urlencoded'
    
    try:
        res = requests.post(url, headers=headers, data=total_params)
        res.raise_for_status()
        result = res.json()
        print(result)
        print(f"[SUCCESS] Order Executed: {side} {quantity} {pair}")
        return res.json()
    except Exception as e:
        print(f"Order Error: {e}")
        print(f"Response text: {e.response.text if e.response else 'N/A'}")

# ==========================================================
# 3. 數據抓取與策略邏輯
# ==========================================================
def get_market_data(symbol, start_dt, end_dt, interval='1m'):
    try:
        s_ms = int(start_dt.timestamp() * 1000)
        e_ms = int(end_dt.timestamp() * 1000) - 1
        klines = binance_client.get_klines(symbol=symbol, interval=interval, startTime=s_ms, endTime=e_ms)
        if not klines: return None
        return {
            'high': max(float(k[2]) for k in klines),
            'low': min(float(k[3]) for k in klines),
            'vol': sum(float(k[5]) for k in klines),
            'price': float(klines[-1][4])
        }
    except: return None

def run_trading_strategy():
    est = pytz.timezone('America/New_York')
    today = datetime.now(est)
    w1_s, w1_e = today.replace(hour=9, minute=0, second=0, microsecond=0), today.replace(hour=9, minute=30, second=0, microsecond=0)
    w2_s, w2_e = today.replace(hour=9, minute=30, second=0, microsecond=0), today.replace(hour=10, minute=0, second=0, microsecond=0)

    best_symbol, max_vol_ratio, target_price = None, 0, 0
    print(f"\n--- 分析開始: {today.strftime('%Y-%m-%d %H:%M:%S')} ---")

    for symbol in roostoo_symbols:
        s1, s2 = get_market_data(symbol, w1_s, w1_e), get_market_data(symbol, w2_s, w2_e)
        if s1 and s2 and s2['high'] > s1['high'] and s2['low'] >= s1['low']:
            vol_7d = get_market_data(symbol, today - timedelta(days=7), today, interval='1d')
            if vol_7d and vol_7d['vol'] > 0:
                ratio = s2['vol'] / vol_7d['vol']
                if ratio > max_vol_ratio:
                    max_vol_ratio, best_symbol, target_price = ratio, symbol, s2['price']
                

    if best_symbol:
        balance_data = get_balance() 
        # --- 這裡加入了提取 50000 (USD Free) 的邏輯 ---
        if balance_data and balance_data.get('Success'):
            spot_wallet = balance_data.get('SpotWallet', {})
            usd_info = spot_wallet.get('USD', {})
            usd_val = float(usd_info.get('Free', 0.0))
            
            print(f"解析成功，目前可用餘額: {usd_val} USD")
            
            if usd_val > 1.0:
                # 使用 98% 的餘額買入，預留手續費
                print(usd_val)
                print(target_price)
                qty = (usd_val / target_price) * 0.98
                print(qty)
                send_roostoo_order(best_symbol, "BUY",int( qty))
                return best_symbol, int(qty)
    return None, 0

# ==========================================================
# 4. 主循環
# ==========================================================
est = pytz.timezone('America/New_York')
current_pos, current_qty = None, 0
buy_executed, sell_executed = False, False
  # 啟動前先清倉，確保測試環境乾淨
#print("Roostoo 自動化交易機器人啟動中...")

# 啟動時先測一次
test_res = get_balance()
print(f"初始連接測試 - 原始 JSON: {test_res}")
balance_data = get_balance() 
        # --- 這裡加入了提取 50000 (USD Free) 的邏輯 ---
if balance_data and balance_data.get('Success'):
    spot_wallet = balance_data.get('SpotWallet', {})
    usd_info = spot_wallet.get('USD', {})
    usd_val = float(usd_info.get('Free', 0.0))
    print(f"解析成功，目前可用餘額: {usd_val} USD")
        # --- 這裡加入了提取 50000 (USD Free) 的邏輯 ---
while True:
    test_res = get_balance()
    print(f"初始連接測試 - 原始 JSON: {test_res}")
    balance_data = get_balance() 
#        # --- 這裡加入了提取 50000 (USD Free) 的邏輯 ---
    if balance_data and balance_data.get('Success'):
        spot_wallet = balance_data.get('SpotWallet', {})
        usd_info = spot_wallet.get('USD', {})
        usd_val = float(usd_info.get('Free', 0.0))
        print(f"解析成功，目前可用餘額: {usd_val} USD")
        print("hi")
        now = datetime.now(est)
        print(now.strftime("%Y-%m-%d %H:%M:%S"))
    
    # 10:45 買入
    if now.hour >= 10 and now.minute >= 00 :
        current_pos, current_qty = run_trading_strategy()
        buy_executed = True
        
    # 16:50 賣出
    if now.hour >= 16 and now.minute == 00 and not sell_executed:
        if current_pos:
            send_roostoo_order(current_pos, "SELL", current_qty)
            current_pos, current_qty = None, 0
        sell_executed = True
            
    if now.hour == 0 and now.minute == 0:
        buy_executed, sell_executed = False, False

    time.sleep(30)
