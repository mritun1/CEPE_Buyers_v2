# backend_full.py
import os
import json
import time
import requests
import asyncio
import websockets
from flask import Flask, jsonify
from flask_cors import CORS
from datetime import datetime as dt
import pytz
from threading import Thread
from findstrikeprice2 import strike_prices, get_instrument_token
from key import SANDBOX_TOKEN, LIVE_TOKEN
import socket
import signal
import sys

# -------------------------
# Configuration / Globals
# -------------------------
ist = pytz.timezone("Asia/Kolkata")

LTP_LOWER_BOUND = 100
LTP_UPPER_BOUND = 200
UNDERLYING_SYMBOL = "NIFTYBANK"
TRADING_MODE = "paper"
LOT_SIZE = 70
STOP_LOSS_OFFSET = 20
TRAIL_OFFSET = 3
TRAIL_THRESHOLD = 2
TRAIL_INITIAL = 1
POLLING_INTERVAL = 2
ENVIRONMENT = "live"

ACCESS_TOKEN = LIVE_TOKEN if ENVIRONMENT == "live" else SANDBOX_TOKEN
USE_LIVE_TRADING = TRADING_MODE == "live"
BASE = "https://api-v2.upstox.com"
HEADERS = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Accept": "application/json"}
BROKERAGE_PER_ORDER = 20
STT_RATE = 0.000625
TRANSACTION_RATE = 0.0003503
STAMP_DUTY_RATE = 0.00003
SEBI_TURNOVER = 10 / 1e7
IPFT_RATE = 0.50 / 1e5
GST_RATE = 0.18

TRADES_LOG_FILE_TEMPLATE = "paper_trades_{mode}_{date}.json"

# -------------------------
# App & state
# -------------------------
app = Flask(__name__, template_folder=".")
CORS(app, resources={r"/api/*": {"origins": "*"}})

log_messages = []
connected_clients = set()

ce_running = False
pe_running = False

ce_trades = []
pe_trades = []
ce_day_pnl = 0.0
pe_day_pnl = 0.0
ce_capital_used = 0.0
pe_capital_used = 0.0

current_balance = 100000.0
chart_data = {"labels": [], "values": []}

websocket_server_thread = None
chart_data_thread = None
shutdown_flag = False

INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json"
INDEX_SYMBOLS = {
    "NIFTYBANK": "NIFTY BANK",
    "NIFTY": "NIFTY 50", 
    "FINNIFTY": "NIFTY FIN SERVICE",
    "SENSEX": "SENSEX"
}

# Global variable to cache instruments
instruments_cache = {}
cache_expiry = None

def signal_handler(sig, frame):
    """Handle shutdown signals"""
    global shutdown_flag, ce_running, pe_running
    log("Received shutdown signal, cleaning up...")
    shutdown_flag = True
    ce_running = False
    pe_running = False
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def download_instruments():
    """Download complete instruments file from Upstox"""
    global instruments_cache, cache_expiry
    
    try:
        log("Downloading instruments file from Upstox...")
        resp = requests.get(INSTRUMENTS_URL, timeout=30)
        resp.raise_for_status()
        
        instruments_data = resp.json()
        log(f"Downloaded {len(instruments_data)} instruments")
        
        # Cache instruments by segment and symbol
        instruments_cache = {}
        for instrument in instruments_data:
            segment = instrument.get('segment', '')
            trading_symbol = instrument.get('trading_symbol', '')
            name = instrument.get('name', '')
            
            key = f"{segment}|{trading_symbol}"
            instruments_cache[key] = instrument
            
            # Also cache by name for indexes
            if segment == 'NSE_INDEX':
                name_key = f"{segment}|{name}"
                instruments_cache[name_key] = instrument
        
        cache_expiry = dt.now().timestamp() + (6 * 60 * 60)  # Cache for 6 hours
        log("Instruments cache updated successfully")
        return True
        
    except Exception as e:
        log(f"Error downloading instruments: {e}")
        return False
    
def get_cached_instrument(segment, symbol):
    """Get instrument from cache"""
    global instruments_cache, cache_expiry
    
    # Check if cache is expired or empty
    if not instruments_cache or not cache_expiry or dt.now().timestamp() > cache_expiry:
        if not download_instruments():
            return None
    
    # Try exact match first
    key = f"{segment}|{symbol}"
    if key in instruments_cache:
        return instruments_cache[key]
    
    # For indexes, also try by name
    if segment == 'NSE_INDEX':
        name_key = f"{segment}|{symbol}"
        if name_key in instruments_cache:
            return instruments_cache[name_key]
    
    return None

def get_index_token_new(index_name=UNDERLYING_SYMBOL):
    """Get index instrument token using the new method"""
    try:
        # Map common names to actual index names
        actual_name = INDEX_SYMBOLS.get(index_name, index_name)
        
        # Try to get from cache
        instrument = get_cached_instrument('NSE_INDEX', actual_name)
        if instrument:
            token = instrument.get('exchange_token')
            instrument_key = instrument.get('instrument_key')
            trading_symbol = instrument.get('trading_symbol')
            log(f"Found index: {trading_symbol} -> Token: {token}, Key: {instrument_key}")
            return token, instrument_key
        
        # If not found, list available NSE_INDEX instruments
        if instruments_cache:
            nse_indexes = []
            for key, instrument in instruments_cache.items():
                if instrument.get('segment') == 'NSE_INDEX':
                    nse_indexes.append({
                        'symbol': instrument.get('trading_symbol', ''),
                        'name': instrument.get('name', ''),
                        'key': instrument.get('instrument_key', ''),
                        'token': instrument.get('exchange_token', '')
                    })
            
            log(f"Available NSE_INDEX instruments: {[idx['name'] for idx in nse_indexes[:10]]}")
            
            # Try to find a partial match
            for idx in nse_indexes:
                if any(term.upper() in idx['name'].upper() for term in ['BANK', 'NIFTY']):
                    if 'BANK' in index_name.upper() and 'BANK' in idx['name'].upper():
                        log(f"Found partial match: {idx['name']} -> {idx['token']}")
                        return idx['token'], idx['key']
        
        log(f"Index {index_name} ({actual_name}) not found")
        return None, None
        
    except Exception as e:
        log(f"Error getting index token: {e}")
        return None, None
    
def get_ltp_with_instrument_key(instrument_key):
    """Get LTP using instrument key"""
    try:
        url = f"{BASE}/market-quote/ltp"
        params = {"instrument_key": instrument_key}
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        
        data = resp.json().get("data", {})
        if data:
            # Get first item from data dict
            instrument_data = next(iter(data.values()))
            ltp = instrument_data.get("last_price")
            log(f"LTP for {instrument_key}: ₹{ltp}")
            return ltp
        else:
            log(f"No LTP data for {instrument_key}")
            return None
            
    except Exception as e:
        log(f"Error fetching LTP for {instrument_key}: {e}")
        return None
    
def fetch_chart_data_new():
    """Fetch chart data using the new method"""
    global chart_data, shutdown_flag
    
    retry_count = 0
    max_retries = 3
    current_token = None
    current_instrument_key = None
    
    while not shutdown_flag:
        try:
            # Get index token if not cached
            if not current_token or not current_instrument_key:
                token, instrument_key = get_index_token_new(UNDERLYING_SYMBOL)
                if not token or not instrument_key:
                    retry_count += 1
                    if retry_count >= max_retries:
                        log("Failed to get index token after max retries, using mock data")
                        # Generate mock chart data
                        now = dt.now(ist)
                        chart_data = {
                            "labels": [(now.replace(minute=i*5)).strftime("%H:%M") for i in range(12)],
                            "values": [25000 + (i * 50) + (i % 3 * 25) for i in range(12)]
                        }
                        retry_count = 0
                        time.sleep(60)
                        continue
                    else:
                        log(f"Failed to get index token, retry {retry_count}/{max_retries}")
                        time.sleep(30)
                        continue
                
                current_token = token
                current_instrument_key = instrument_key
                retry_count = 0
            
            # Fetch OHLC data using instrument key
            url = f"{BASE}/market-quote/ohlc"
            params = {
                "instrument_key": current_instrument_key,
                "interval": "1minute"
            }
            
            resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
            
            if resp.status_code == 200:
                response_data = resp.json()
                data_key = current_instrument_key
                ohlc_data = response_data.get("data", {}).get(data_key, [])
                
                if ohlc_data and isinstance(ohlc_data, list):
                    labels, values = [], []
                    
                    # Process last 20 data points
                    for item in ohlc_data[-20:]:
                        timestamp_str = item.get("timestamp", "").rstrip("Z")
                        try:
                            if timestamp_str:
                                dt_obj = dt.fromisoformat(timestamp_str)
                                labels.append(dt_obj.astimezone(ist).strftime("%H:%M"))
                            else:
                                labels.append(dt.now(ist).strftime("%H:%M"))
                        except Exception:
                            labels.append(dt.now(ist).strftime("%H:%M"))
                        
                        ohlc = item.get("ohlc", {})
                        close_price = ohlc.get("close", 0)
                        values.append(float(close_price) if close_price else 25000)
                    
                    if labels and values:
                        chart_data["labels"] = labels
                        chart_data["values"] = values
                        log(f"Chart data updated: {len(values)} points, latest: ₹{values[-1]}")
                    else:
                        log("No valid OHLC data extracted")
                
                elif response_data.get("status") == "success":
                    # If no OHLC data, try to get current LTP
                    ltp = get_ltp_with_instrument_key(current_instrument_key)
                    if ltp:
                        # Update with single current price point
                        current_time = dt.now(ist).strftime("%H:%M")
                        if chart_data.get("labels") and chart_data.get("values"):
                            chart_data["labels"].append(current_time)
                            chart_data["values"].append(ltp)
                            # Keep only last 20 points
                            if len(chart_data["labels"]) > 20:
                                chart_data["labels"] = chart_data["labels"][-20:]
                                chart_data["values"] = chart_data["values"][-20:]
                        else:
                            chart_data["labels"] = [current_time]
                            chart_data["values"] = [ltp]
                        log(f"Chart updated with current LTP: ₹{ltp}")
                
            elif resp.status_code == 404:
                log("Chart data endpoint returned 404, trying to refresh instrument token")
                current_token = None
                current_instrument_key = None
                time.sleep(30)
                continue
            else:
                log(f"Chart data API returned {resp.status_code}: {resp.text}")
                time.sleep(30)
                continue
            
            time.sleep(15)  # Wait 15 seconds between updates
            
        except requests.exceptions.Timeout:
            log("Timeout fetching chart data")
            time.sleep(30)
        except requests.exceptions.RequestException as e:
            log(f"Request error fetching chart data: {e}")
            time.sleep(30)
        except Exception as e:
            log(f"Unexpected error in chart fetch: {e}")
            time.sleep(30)

def test_index_data():
    """Test function to verify index data access"""
    log("Testing index data access...")
    
    # Test getting index token
    token, instrument_key = get_index_token_new(UNDERLYING_SYMBOL)
    if token and instrument_key:
        log(f"✓ Successfully got index token: {token}, key: {instrument_key}")
        
        # Test getting LTP
        ltp = get_ltp_with_instrument_key(instrument_key)
        if ltp:
            log(f"✓ Successfully got LTP: ₹{ltp}")
            return True
        else:
            log("✗ Failed to get LTP")
    else:
        log("✗ Failed to get index token")
    
    return False

# -------------------------
# Logging & async broadcast
# -------------------------
def log(msg: str):
    timestamp = dt.now(ist).strftime("%Y-%m-%d %H:%M:%S")
    mode_ind = "🔴 LIVE" if USE_LIVE_TRADING else "📝 PAPER"
    message = f"{timestamp} [{mode_ind}] - {msg}"
    print(message)
    log_messages.append({"timestamp": timestamp, "message": message})
    
    # Keep only last 100 log messages to prevent memory issues
    if len(log_messages) > 100:
        log_messages[:] = log_messages[-100:]

async def broadcast_message(message: dict):
    """Safely broadcast message to all connected WebSocket clients"""
    if not connected_clients:
        return
    
    payload = json.dumps(message)
    disconnected_clients = []
    
    # Create a copy of the set to avoid modification during iteration
    clients_copy = connected_clients.copy()
    
    for ws in clients_copy:
        try:
            await ws.send(payload)
        except websockets.exceptions.ConnectionClosed:
            disconnected_clients.append(ws)
        except Exception as e:
            print(f"Error broadcasting to client: {e}")
            disconnected_clients.append(ws)
    
    # Remove disconnected clients
    for ws in disconnected_clients:
        connected_clients.discard(ws)

# -------------------------
# Index / Chart fetcher
# -------------------------
def get_index_token(client, index_name=UNDERLYING_SYMBOL):
    """Wrapper for backward compatibility"""
    token, instrument_key = get_index_token_new(index_name)
    return token

def fetch_chart_data():
    """Updated chart data fetcher"""
    fetch_chart_data_new()

def initialize_instruments():
    """Initialize instruments on startup"""
    log("Initializing instruments data...")
    if download_instruments():
        if test_index_data():
            log("✓ Instruments initialization successful")
            return True
        else:
            log("✗ Instruments test failed")
    else:
        log("✗ Failed to download instruments")
    return False

# -------------------------
# Instrument caching
# -------------------------
def get_json_file(mode: str):
    mode = (mode or "PE").upper()
    return f"instrument_data_{mode}.json"

def save_instrument_data(instrument_key, strike_ce, strike_pe, expiry, mode: str):
    file = get_json_file(mode)
    data = {"instrument_key": instrument_key, "strike_ce": strike_ce, "strike_pe": strike_pe, "expiry": expiry, "created_at": dt.now(ist).isoformat()}
    try:
        with open(file, "w") as f:
            json.dump(data, f, indent=2)
        log(f"Saved instrument data to {file}")
    except Exception as e:
        log(f"Error saving JSON {file}: {e}")

def load_instrument_data(mode: str):
    file = get_json_file(mode)
    if os.path.exists(file):
        try:
            with open(file, "r") as f:
                return json.load(f)
        except Exception as e:
            log(f"Error loading JSON {file}: {e}")
            return None
    return None

def get_new_instrument(mode: str = "PE"):
    mode = (mode or "PE").upper()
    try:
        with requests.Session() as client:
            log(f"Fetching new {mode} instrument")
            strikes = strike_prices(client, LTP_LOWER_BOUND, LTP_UPPER_BOUND)
            expiry, ce_strike, pe_strike = strikes
            if expiry == "0":
                log("Failed to fetch strikes")
                return None, None, None
            strike = ce_strike if mode == "CE" else pe_strike
            token = get_instrument_token(client, expiry, strike, mode)
            if not token:
                log("Failed to get instrument token")
                return None, None, None
            instrument_key = f"NSE_FO|{token}"
            save_instrument_data(instrument_key, ce_strike, pe_strike, expiry, mode)
            log(f"Generated new {mode} instrument: {instrument_key} (strike: {strike}, expiry: {expiry})")
            return instrument_key, ce_strike, pe_strike
    except Exception as e:
        log(f"Error in get_new_instrument: {e}")
        return None, None, None

# -------------------------
# LTP fetcher
# -------------------------
def get_ltp_rest(key: str):
    try:
        url = f"{BASE}/market-quote/ltp"
        params = {"instrument_key": key}
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        if not data:
            log(f"No LTP data for {key}")
            return None
        _, info = next(iter(data.items()))
        ltp = info.get("last_price")
        log(f"LTP for {key}: ₹{ltp}")
        return ltp
    except Exception as e:
        log(f"Error fetching LTP for {key}: {e}")
        return None

# -------------------------
# Charges & trade saving
# -------------------------
def calculate_charges(entry_price, exit_price, qty):
    turnover = (entry_price + exit_price) * qty
    brokerage = 2 * BROKERAGE_PER_ORDER
    stt = abs(exit_price) * qty * STT_RATE
    txn_charges = turnover * TRANSACTION_RATE
    stamp_duty = entry_price * qty * STAMP_DUTY_RATE
    sebi = turnover * SEBI_TURNOVER
    ipft = turnover * IPFT_RATE
    gst = GST_RATE * (brokerage + txn_charges)
    total = brokerage + stt + txn_charges + stamp_duty + sebi + ipft + gst
    return {"brokerage": brokerage, "stt": stt, "txn_charges": txn_charges,
            "stamp_duty": stamp_duty, "sebi": sebi, "ipft": ipft, "gst": gst, "total": total}

def get_trades_log_file(mode: str):
    mode = (mode or "PE").upper()
    return TRADES_LOG_FILE_TEMPLATE.format(mode=mode, date=dt.now().strftime("%d%b%Y").upper())

def save_trade_to_file(trade_data, mode: str):
    file_name = get_trades_log_file(mode)
    try:
        trades = []
        if os.path.exists(file_name):
            with open(file_name, "r") as f:
                trades = json.load(f)
        trades.append(trade_data)
        with open(file_name, "w") as f:
            json.dump(trades, f, indent=2)
        log(f"Saved trade to {file_name}")
    except Exception as e:
        log(f"Error saving trade to file {file_name}: {e}")

# -------------------------
# Place order logic
# -------------------------
def place_order(instrument_key: str, side: str, price: float = None, mode: str = "PE"):
    global ce_trades, pe_trades, ce_day_pnl, pe_day_pnl, ce_capital_used, pe_capital_used, current_balance
    mode = (mode or "PE").upper()
    current_price = price or get_ltp_rest(instrument_key)
    if current_price is None:
        log(f"[{mode}] Failed to get price for {side} order")
        return False

    trade_data = {"timestamp": dt.now(ist).isoformat(), "instrument_key": instrument_key,
                  "strike_price_mode": mode, "action": side.upper(), "quantity": LOT_SIZE,
                  "price": round(current_price, 2), "order_type": "MARKET",
                  "mode": "LIVE" if USE_LIVE_TRADING else "PAPER", "charges": {}, "gross_profit": None,
                  "net_profit": None, "day_pnl": None}

    # PAPER mode
    if not USE_LIVE_TRADING:
        target_list = ce_trades if mode == "CE" else pe_trades
        target_list.append(trade_data)
        save_trade_to_file(trade_data, mode)
        log(f"[PAPER] {mode} {side} {LOT_SIZE} of {instrument_key} @ ₹{trade_data['price']}")
        
        # SELL PnL calculation
        if side.upper() == "SELL":
            last_buy = next((t for t in reversed(target_list) if t["instrument_key"] == instrument_key and t["action"]=="BUY"), None)
            if last_buy:
                entry_price = last_buy["price"]
                exit_price = trade_data["price"]
                gross_profit = round((exit_price - entry_price) * LOT_SIZE, 2)
                charges = calculate_charges(entry_price, exit_price, LOT_SIZE)
                charges = {k: round(v,2) for k,v in charges.items()}
                net_profit = round(gross_profit - charges["total"],2)
                trade_data.update({"gross_profit": gross_profit, "net_profit": net_profit, "charges": charges})
                if mode=="CE":
                    ce_day_pnl += net_profit
                    ce_capital_used += entry_price*LOT_SIZE
                else:
                    pe_day_pnl += net_profit
                    pe_capital_used += entry_price*LOT_SIZE
                current_balance += net_profit
                trade_data["day_pnl"] = ce_day_pnl if mode=="CE" else pe_day_pnl
                log(f"[PAPER] {mode} Trade Summary: Entry ₹{entry_price}, Exit ₹{exit_price}, Net ₹{net_profit}")
        
        # Simplified broadcast - let the WebSocket server handle its own loop
        # Just store the data, clients can poll for updates
        return True

    # LIVE mode
    else:
        try:
            payload = {"instrument_key": instrument_key, "quantity": LOT_SIZE, "product": "I",
                       "order_type": "MARKET", "transaction_type": side.upper(), "validity": "DAY"}
            resp = requests.post(f"{BASE}/order/place", headers=HEADERS, json=payload, timeout=10)
            resp.raise_for_status()
            log(f"[LIVE] {mode} {side} order placed @ ₹{trade_data['price']}")
            target_list = ce_trades if mode=="CE" else pe_trades
            target_list.append(trade_data)
            save_trade_to_file(trade_data, mode)
            return True
        except Exception as e:
            log(f"[LIVE] Error placing order: {e}")
            return False

# -------------------------
# Summary
# -------------------------
def get_full_summary():
    overall_day_pnl = ce_day_pnl + pe_day_pnl
    overall_capital_used = ce_capital_used + pe_capital_used
    all_trades = (ce_trades or []) + (pe_trades or [])
    overall_charges = sum(t.get("charges", {}).get("total",0) for t in all_trades)
    return {"ce":{"day_pnl":ce_day_pnl,"capital_used":ce_capital_used,"trades":ce_trades},
            "pe":{"day_pnl":pe_day_pnl,"capital_used":pe_capital_used,"trades":pe_trades},
            "overall":{"day_pnl":overall_day_pnl,"capital_used":overall_capital_used,
                       "charges": round(overall_charges,2), "current_balance": round(current_balance,2)}}

# -------------------------
# Strategy class
# -------------------------
class OptionStrategy:
    def __init__(self, mode: str):
        self.mode = (mode or "PE").upper()
        self.instrument_key = None
        self.bought = False
        self.entry = None
        self.stop = None
        self.peak = None
        self.prev_price = None
        self.running = True
        inst = load_instrument_data(self.mode)
        if inst:
            self.instrument_key = inst.get("instrument_key")
            self.prev_price = get_ltp_rest(self.instrument_key)
            log(f"[{self.mode}] Loaded cached instrument {self.instrument_key}")
        else:
            log(f"[{self.mode}] No cached instrument found")

    def reset_position(self):
        self.bought = False
        self.entry = None
        self.stop = None
        self.peak = None

    def ensure_instrument(self):
        retry_delay = 2
        while self.running:
            new_key, ce_strike, pe_strike = get_new_instrument(mode=self.mode)
            if not new_key:
                log(f"[{self.mode}] No instrument, retrying {retry_delay}s")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay*2,60)
                continue
            ltp = get_ltp_rest(new_key)
            if ltp and LTP_LOWER_BOUND <= ltp <= LTP_UPPER_BOUND:
                self.instrument_key = new_key
                self.prev_price = ltp
                log(f"[{self.mode}] Selected instrument {new_key} with LTP {ltp}")
                return
            else:
                log(f"[{self.mode}] Rejected {new_key} LTP={ltp}, retrying {retry_delay}s")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay*2,60)

    def process_price_update(self, ltp):
        if ltp is None:
            return
        if ltp < LTP_LOWER_BOUND or ltp > LTP_UPPER_BOUND:
            log(f"[{self.mode}] LTP {ltp} out of range, resetting")
            self.reset_position()
            self.ensure_instrument()
            return
        if not self.bought and self.prev_price is not None and ltp>self.prev_price:
            if place_order(self.instrument_key,"BUY",ltp,self.mode):
                self.bought=True
                self.entry=ltp
                self.stop=ltp-STOP_LOSS_OFFSET
                self.peak=ltp
                log(f"[{self.mode}] Position opened @ {ltp}")
        elif self.bought:
            if ltp > self.peak:
                self.peak = ltp
            trail_exit = TRAIL_OFFSET
            if ltp <= self.entry + TRAIL_THRESHOLD:
                trail_exit = TRAIL_INITIAL
            trail_stop = self.peak - trail_exit
            if ltp <= trail_stop or ltp <= self.stop:
                if place_order(self.instrument_key, "SELL", ltp, self.mode):
                    log(f"[{self.mode}] Position closed @ {ltp}")
                self.reset_position()
        self.prev_price = ltp

# -------------------------
# Option loop
# -------------------------
def run_option_loop(mode: str):
    strategy = OptionStrategy(mode)
    if not strategy.instrument_key:
        strategy.ensure_instrument()
    last_price_time = dt.now(ist)
    errors = 0
    while (ce_running if mode == "CE" else pe_running) and strategy.running:
        try:
            ltp = get_ltp_rest(strategy.instrument_key)
            if ltp:
                strategy.process_price_update(ltp)
                errors = 0
                last_price_time = dt.now(ist)
            else:
                errors += 1
                if errors >= 3:
                    log(f"[{mode}] Too many LTP errors, reset")
                    strategy.reset_position()
                    strategy.ensure_instrument()
                    errors = 0
            if (dt.now(ist) - last_price_time).total_seconds() > 60:
                log(f"[{mode}] No price updates 60s, reset")
                strategy.reset_position()
                strategy.ensure_instrument()
                last_price_time = dt.now(ist)
            time.sleep(POLLING_INTERVAL)
        except Exception as e:
            log(f"[{mode}] Error in loop: {e}")
            time.sleep(5)
    if strategy.bought:
        exit_price = get_ltp_rest(strategy.instrument_key)
        if exit_price:
            place_order(strategy.instrument_key, "SELL", exit_price, mode)
            log(f"[{mode}] Squared off @ {exit_price}")
    log(f"[{mode}] Loop ended")

# -------------------------
# WebSocket
# -------------------------
async def websocket_handler(websocket):
    """Handle WebSocket connections - simplified signature"""
    connected_clients.add(websocket)
    log("WebSocket client connected")
    try:
        # Send initial data
        await websocket.send(json.dumps({
            "type": "initial", 
            "data": {
                "summary": get_full_summary(),
                "chart": chart_data,
                "logs": log_messages[-10:]  # Last 10 messages
            }
        }))
        
        # Keep connection alive and handle incoming messages
        async for message in websocket:
            try:
                data = json.loads(message)
                # Echo back acknowledgment
                await websocket.send(json.dumps({
                    "type": "ack", 
                    "data": f"Received: {data.get('type', 'unknown')}"
                }))
            except json.JSONDecodeError:
                await websocket.send(json.dumps({
                    "type": "error", 
                    "data": "Invalid JSON"
                }))
                
    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as e:
        log(f"WebSocket client error: {e}")
    finally:
        connected_clients.discard(websocket)
        log("WebSocket client disconnected")

def is_port_in_use(port):
    """Check if a port is already in use"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('0.0.0.0', port))
            return False
        except socket.error:
            return True

def kill_process_on_port(port):
    """Kill any process using the specified port"""
    import subprocess
    try:
        # Find process using the port
        result = subprocess.run(['lsof', '-ti', f':{port}'], 
                              capture_output=True, text=True, check=False)
        if result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                try:
                    subprocess.run(['kill', '-9', pid], check=False)
                    log(f"Killed process {pid} on port {port}")
                except Exception as e:
                    log(f"Failed to kill process {pid}: {e}")
        return True
    except Exception as e:
        log(f"Error cleaning up port {port}: {e}")
        return False

def start_websocket_server():
    """Start WebSocket server with proper cleanup and error handling"""
    websocket_port = 8765
    
    # Check if port is in use and try to clean it up
    if is_port_in_use(websocket_port):
        log(f"Port {websocket_port} is in use, attempting to free it...")
        if kill_process_on_port(websocket_port):
            time.sleep(2)  # Wait a bit for cleanup
        else:
            log(f"Could not free port {websocket_port}, trying alternative port")
            websocket_port = 8766  # Try alternative port
    
    # Create and set event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    async def run_server():
        server = None
        try:
            # Start the WebSocket server
            server = await websockets.serve(
                websocket_handler, 
                "0.0.0.0", 
                websocket_port,
                ping_interval=30,
                ping_timeout=10
            )
            log(f"WebSocket server started on ws://0.0.0.0:{websocket_port}")
            
            # Keep server running
            await server.wait_closed()
            
        except Exception as e:
            log(f"WebSocket server error: {e}")
        finally:
            if server:
                server.close()
                await server.wait_closed()
    
    try:
        loop.run_until_complete(run_server())
    except KeyboardInterrupt:
        log("WebSocket server stopped by user")
    except Exception as e:
        log(f"WebSocket server failed: {e}")
    finally:
        try:
            # Cancel all remaining tasks
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        finally:
            loop.close()

# -------------------------
# REST APIs
# -------------------------
@app.route("/api/chart")
def api_chart():
    return jsonify(chart_data)

@app.route("/api/summary")
def api_summary():
    return jsonify(get_full_summary())

@app.route("/api/logs")
def api_logs():
    return jsonify(log_messages)

@app.route("/api/start")
def api_start():
    global ce_running, pe_running
    if not ce_running:
        ce_running = True
        Thread(target=run_option_loop, args=("CE",), daemon=True).start()
    if not pe_running:
        pe_running = True
        Thread(target=run_option_loop, args=("PE",), daemon=True).start()

    return jsonify({"status":"started", "market_status": "LIVE" if USE_LIVE_TRADING else "PAPER"})

@app.route("/api/stop")
def api_stop():
    global ce_running, pe_running
    ce_running = False
    pe_running = False
    return jsonify({"status":"stopped"})

@app.route("/api/market_status")
def api_market_status():
    status = "LIVE" if USE_LIVE_TRADING else "PAPER"
    return jsonify({"status": status})

@app.route("/api/ce_pe_details")
def api_ce_pe_details():
    ce_data = {"trades": ce_trades, "day_pnl": ce_day_pnl, "capital_used": ce_capital_used}
    pe_data = {"trades": pe_trades, "day_pnl": pe_day_pnl, "capital_used": pe_capital_used}
    return jsonify({"ce": ce_data, "pe": pe_data})

def simple_broadcast(message_type, data):
    """Simple broadcast that doesn't interfere with asyncio"""
    # Just store the latest data - WebSocket clients can poll via REST API
    # This avoids the asyncio/threading complications
    pass

# -------------------------
# Main
# -------------------------
if __name__ == "__main__":
    try:
        log("Starting trading application...")
        
        # Initialize instruments first
        initialize_instruments()
        
        # Start chart data fetching thread
        chart_data_thread = Thread(target=fetch_chart_data, daemon=True, name="ChartDataThread")
        chart_data_thread.start()
        log("Chart data thread started")
        
        # Start WebSocket server thread  
        websocket_server_thread = Thread(target=start_websocket_server, daemon=True, name="WebSocketThread")
        websocket_server_thread.start()
        log("WebSocket server thread started")
        
        # Wait for initialization
        time.sleep(3)
        
        # Start Flask app
        log("Starting Flask application...")
        app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
        
    except KeyboardInterrupt:
        log("Application interrupted by user")
    except Exception as e:
        log(f"Application startup error: {e}")
    finally:
        shutdown_flag = True
        log("Application shutdown complete")

