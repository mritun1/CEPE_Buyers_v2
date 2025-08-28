import time
import json
import requests
import asyncio
import websockets
from flask import Flask, render_template, jsonify
from flask_cors import CORS
from datetime import datetime as dt, time as dtime
import pytz
from findstrikeprice import strike_prices, get_instrument_token
from key import SANDBOX_TOKEN, LIVE_TOKEN
import os

# Global variables
log_messages = []
paper_trades = []
connected_clients = set()
day_pnl = 0.0
capital_used = 0.0
current_instrument_valid = True
chart_data = {'labels': [], 'values': []}
ist = pytz.timezone('Asia/Kolkata')

# Configuration
LTP_LOWER_BOUND = 100
LTP_UPPER_BOUND = 200
UNDERLYING_INSTRUMENT = "NSE_INDEX|Nifty Bank"
STRIKE_PRICE_MODE = "PE"
TRADING_MODE = "paper"
LOT_SIZE = 70
STOP_LOSS_OFFSET = 20
TRAIL_OFFSET = 3
ENVIRONMENT = "live"
JSON_FILE = "instrument_data_" + STRIKE_PRICE_MODE + ".json"
ACCESS_TOKEN = LIVE_TOKEN if ENVIRONMENT == "live" else SANDBOX_TOKEN
USE_LIVE_TRADING = TRADING_MODE == "live"
TRAIL_THRESHOLD = 2
TRAIL_INITIAL = 1
POLLING_INTERVAL = 2
BASE = "https://api-v2.upstox.com"
HEADERS = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Accept": "application/json"}
TRADES_LOG_FILE = "paper_trades_" + STRIKE_PRICE_MODE + "_" + dt.now().strftime("%d%b%Y").upper() + ".json"
BROKERAGE_PER_ORDER = 20
STT_RATE = 0.000625
TRANSACTION_RATE = 0.0003503
STAMP_DUTY_RATE = 0.00003
SEBI_TURNOVER = 10 / 1e7
IPFT_RATE = 0.50 / 1e5
GST_RATE = 0.18

# Flask app
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

def log(msg):
    global log_messages
    timestamp = dt.now(ist).strftime("%Y-%m-%d %H:%M:%S")
    mode_indicator = "🔴 LIVE" if USE_LIVE_TRADING else "📝 PAPER"
    message = f"{timestamp} [{mode_indicator}] - {msg}"
    print(message)
    log_messages.append({"timestamp": timestamp, "message": message})
    if connected_clients:
        asyncio.create_task(broadcast_message({"type": "log", "data": message}))

async def broadcast_message(message):
    if connected_clients:
        try:
            await asyncio.gather(*(client.send(json.dumps(message)) for client in connected_clients))
        except Exception as e:
            log(f"Error broadcasting WebSocket message: {e}")

async def websocket_handler(websocket, path):
    global log_messages, paper_trades, day_pnl, capital_used, chart_data
    connected_clients.add(websocket)
    log(f"New WebSocket connection on path: {path}")
    try:
        # Send initial data
        for log in log_messages[-50:]:
            await websocket.send(json.dumps({"type": "log", "data": log["message"]}))
        await websocket.send(json.dumps({"type": "trades", "data": paper_trades}))
        await websocket.send(json.dumps({
            "type": "summary",
            "data": {
                "day_pnl": day_pnl,
                "capital_used": capital_used,
                "return_pct": (day_pnl / capital_used * 100) if capital_used > 0 else 0.0
            }
        }))
        await websocket.send(json.dumps({"type": "chart", "data": chart_data}))
        async for message in websocket:
            log(f"Received WebSocket message: {message}")
    except websockets.exceptions.ConnectionClosed:
        log("WebSocket connection closed")
    finally:
        connected_clients.remove(websocket)
        log("WebSocket client removed")

async def start_websocket_server():
    log("Starting WebSocket server on ws://localhost:8765")
    try:
        server = await websockets.serve(websocket_handler, "localhost", 8765)
        log("WebSocket server started")
        await server.wait_closed()
    except Exception as e:
        log(f"Error starting WebSocket server: {e}")

@app.route('/')
def index():
    try:
        return render_template('index.html')
    except Exception as e:
        log(f"Error serving index.html: {e}")
        return jsonify({"error": "Failed to load dashboard"}), 500

@app.route('/api/logs', methods=['GET'])
def get_logs():
    log("Serving /api/logs")
    return jsonify(log_messages[-50:])

@app.route('/api/trades', methods=['GET'])
def get_trades():
    log("Serving /api/trades")
    return jsonify(paper_trades)

@app.route('/api/summary', methods=['GET'])
def get_summary():
    log("Serving /api/summary")
    return jsonify({
        "day_pnl": day_pnl,
        "capital_used": capital_used,
        "return_pct": (day_pnl / capital_used * 100) if capital_used > 0 else 0.0
    })

@app.route('/api/chart', methods=['GET'])
def get_chart():
    log("Serving /api/chart")
    return jsonify(chart_data)

def is_market_open(now=None):
    # Bypass market hours for testing
    log("Bypassing market hours check for testing")
    return True
    # Original market hours check (uncomment for production)
    # now = now or dt.now(ist)
    # market_open = dtime(9, 20) <= now.time() <= dtime(15, 30)
    # log(f"Market status: {'Open' if market_open else 'Closed'} at {now}")
    # return market_open

def fetch_chart_data():
    global chart_data
    while True:
        # if not is_market_open():
        #     log("Market closed, pausing chart data fetch")
        #     time.sleep(60)
        #     continue
        try:
            url = f"{BASE}/market-quote/ohlc"
            params = {"instrument_key": UNDERLYING_INSTRUMENT, "interval": "1minute"}
            response = requests.get(url, headers=HEADERS, params=params, timeout=10)
            response.raise_for_status()
            data = response.json().get("data", {}).get(UNDERLYING_INSTRUMENT, [])
            if data:
                chart_data['labels'] = [dt.fromisoformat(d['timestamp'][:-1]).astimezone(ist).strftime('%H:%M') for d in data]
                chart_data['values'] = [float(d['ohlc']['close']) for d in data]
                if connected_clients:
                    asyncio.create_task(broadcast_message({"type": "chart", "data": chart_data}))
                log("Chart data updated")
            time.sleep(10)
        except Exception as e:
            log(f"Error fetching chart data: {e}")
            time.sleep(60)

def get_ltp_rest(key):
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
        log(f"Error fetching LTP: {e}")
        return None

def calculate_charges(entry_price, exit_price, qty):
    turnover = (entry_price + exit_price) * qty
    brokerage = 2 * BROKERAGE_PER_ORDER
    stt = exit_price * qty * STT_RATE
    txn_charges = turnover * TRANSACTION_RATE
    stamp_duty = entry_price * qty * STAMP_DUTY_RATE
    sebi = turnover * SEBI_TURNOVER
    ipft = turnover * IPFT_RATE
    gst = GST_RATE * (brokerage + txn_charges)
    total = brokerage + stt + txn_charges + stamp_duty + sebi + ipft + gst
    return {
        "brokerage": brokerage,
        "stt": stt,
        "txn_charges": txn_charges,
        "stamp_duty": stamp_duty,
        "sebi": sebi,
        "ipft": ipft,
        "gst": gst,
        "total": total
    }

def save_trade(trade_data, mode="PAPER"):
    file_name = TRADES_LOG_FILE if mode == "PAPER" else f"live_trades_{STRIKE_PRICE_MODE}_{dt.now().strftime('%d%b%Y').upper()}.json"
    try:
        if os.path.exists(file_name):
            with open(file_name, "r") as f:
                trades = json.load(f)
        else:
            trades = []
        trades.append(trade_data)
        with open(file_name, "w") as f:
            json.dump(trades, f, indent=2)
        global paper_trades
        paper_trades = trades
        if connected_clients:
            asyncio.create_task(broadcast_message({"type": "trades", "data": paper_trades}))
    except Exception as e:
        log(f"Error saving trade: {e}")

def place_order(key, side, price=None):
    global paper_trades, day_pnl, capital_used
    current_price = price or get_ltp_rest(key)
    if not current_price:
        log(f"[{'LIVE' if USE_LIVE_TRADING else 'PAPER'}] Failed to get price for {side} order")
        return False
    mode = "LIVE" if USE_LIVE_TRADING else "PAPER"
    trade_data = {
        "timestamp": dt.now(ist).isoformat(),
        "instrument_key": key,
        "strike_price_mode": STRIKE_PRICE_MODE,
        "action": side.upper(),
        "quantity": LOT_SIZE,
        "price": round(current_price, 2),
        "order_type": "MARKET",
        "mode": mode,
        "charges": {},
        "gross_profit": None,
        "net_profit": None,
        "day_pnl": None
    }
    if not USE_LIVE_TRADING:
        paper_trades.append(trade_data)
        log(f"[PAPER] {side} {LOT_SIZE} of {key} @ ₹{trade_data['price']}")
        if side.upper() == "SELL":
            last_buy = next((t for t in reversed(paper_trades) if t["instrument_key"] == key and t["action"] == "BUY"), None)
            if last_buy:
                entry_price = last_buy["price"]
                exit_price = trade_data["price"]
                gross_profit = round((exit_price - entry_price) * LOT_SIZE, 2)
                charges = calculate_charges(entry_price, exit_price, LOT_SIZE)
                charges = {k: round(v, 2) for k, v in charges.items()}
                net_profit = round(gross_profit - charges["total"], 2)
                trade_data["gross_profit"] = gross_profit
                trade_data["net_profit"] = net_profit
                trade_data["charges"] = charges
                day_pnl += net_profit
                capital_used += entry_price * LOT_SIZE
                trade_data["day_pnl"] = day_pnl
                log(f"Trade Summary: Entry ₹{entry_price}, Exit ₹{exit_price}, Qty {LOT_SIZE}")
                log(f"Gross PnL = ₹{gross_profit}, Net PnL = ₹{net_profit}, Day Total = ₹{day_pnl}")
        save_trade(trade_data, "PAPER")
        if connected_clients:
            asyncio.create_task(broadcast_message({"type": "trades", "data": paper_trades}))
            asyncio.create_task(broadcast_message({
                "type": "summary",
                "data": {
                    "day_pnl": day_pnl,
                    "capital_used": capital_used,
                    "return_pct": (day_pnl / capital_used * 100) if capital_used > 0 else 0.0
                }
            }))
        return True
    else:
        url = f"{BASE}/order/place"
        payload = {
            "instrument_key": key,
            "quantity": LOT_SIZE,
            "product": "I",
            "order_type": "MARKET",
            "transaction_type": side,
            "validity": "DAY"
        }
        try:
            resp = requests.post(url, headers=HEADERS, json=payload, timeout=10)
            resp.raise_for_status()
            log(f"[LIVE] {side} order placed successfully @ ₹{trade_data['price']}")
            if side.upper() == "SELL":
                last_buy = next((t for t in reversed(paper_trades) if t["instrument_key"] == key and t["action"] == "BUY"), None)
                if last_buy:
                    entry_price = last_buy["price"]
                    exit_price = trade_data["price"]
                    gross_profit = round((exit_price - entry_price) * LOT_SIZE, 2)
                    charges = calculate_charges(entry_price, exit_price, LOT_SIZE)
                    charges = {k: round(v, 2) for k, v in charges.items()}
                    net_profit = round(gross_profit - charges["total"], 2)
                    trade_data["gross_profit"] = gross_profit
                    trade_data["net_profit"] = net_profit
                    trade_data["charges"] = charges
                    day_pnl += net_profit
                    capital_used += entry_price * LOT_SIZE
                    trade_data["day_pnl"] = day_pnl
                    log(f"(LIVE) Trade Summary: Entry ₹{entry_price}, Exit ₹{exit_price}, Qty {LOT_SIZE}")
                    log(f"Gross PnL = ₹{gross_profit}, Net PnL = ₹{net_profit}, Day Total = ₹{day_pnl}")
            save_trade(trade_data, "LIVE")
            if connected_clients:
                asyncio.create_task(broadcast_message({"type": "trades", "data": paper_trades}))
                asyncio.create_task(broadcast_message({
                    "type": "summary",
                    "data": {
                        "day_pnl": day_pnl,
                        "capital_used": capital_used,
                        "return_pct": (day_pnl / capital_used * 100) if capital_used > 0 else 0.0
                    }
                }))
            return True
        except requests.exceptions.HTTPError as e:
            log(f"HTTP error placing order: {e}")
            return False
        except Exception as e:
            log(f"Error placing order: {e}")
            return False

def load_instrument_data():
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            log(f"Error loading JSON: {e}")
            return None
    return None

def save_instrument_data(instrument_key, strike_ce, strike_pe, expiry):
    data = {
        "instrument_key": instrument_key,
        "strike_ce": strike_ce,
        "strike_pe": strike_pe,
        "expiry": expiry,
        "created_at": dt.now(ist).isoformat()
    }
    try:
        with open(JSON_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        log(f"Saved instrument data to {JSON_FILE}")
    except Exception as e:
        log(f"Error saving JSON: {e}")

def get_new_instrument(underlying_price=25000):
    global current_instrument_valid
    try:
        with requests.Session() as client:
            log("Fetching new instrument")
            strikes = strike_prices(client, LTP_LOWER_BOUND, LTP_UPPER_BOUND)
            expiry, ce_strike, pe_strike = strikes
            if expiry == "0":
                log("Failed to fetch strikes")
                return None, None, None
            token = get_instrument_token(client, expiry, pe_strike, STRIKE_PRICE_MODE)
            if not token:
                log("Failed to get instrument token")
                return None, None, None
            instrument_key = f"NSE_FO|{token}"
            save_instrument_data(instrument_key, ce_strike, pe_strike, expiry)
            log(f"Generated new instrument: {instrument_key} ({STRIKE_PRICE_MODE} strike: {pe_strike}, expiry: {expiry})")
            current_instrument_valid = True
            return instrument_key, ce_strike, pe_strike
    except Exception as e:
        log(f"Error in get_new_instrument: {e}")
        return None, None, None

class TradingStrategy:
    def __init__(self, instrument_key):
        self.instrument_key = instrument_key
        self.reset_position()
        self.running = True
        self.prev_price = get_ltp_rest(instrument_key)
        if self.prev_price:
            log(f"Initial LTP: ₹{self.prev_price}")
        else:
            log("Could not get initial price")
            self.running = False

    def reset_position(self):
        self.bought = False
        self.entry = None
        self.stop = None
        self.peak = None
        self.prev_price = None

    def find_new_instrument(self):
        global current_instrument_valid
        retry_delay = 2
        while True:
            log("Trying to find a new instrument")
            new_key, ce_strike, pe_strike = get_new_instrument()
            if not new_key:
                log(f"No instrument found, retrying in {retry_delay}s")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
                continue
            ltp = get_ltp_rest(new_key)
            if ltp and LTP_LOWER_BOUND <= ltp <= LTP_UPPER_BOUND:
                log(f"Found valid instrument {new_key} with LTP {ltp}")
                self.instrument_key = new_key
                self.prev_price = ltp
                current_instrument_valid = True
                return
            else:
                log(f"Instrument {new_key} rejected (LTP={ltp}), retrying in {retry_delay}s")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

    def process_price_update(self, ltp):
        if ltp < LTP_LOWER_BOUND or ltp > LTP_UPPER_BOUND:
            log(f"LTP {ltp} outside range [{LTP_LOWER_BOUND}-{LTP_UPPER_BOUND}]")
            self.reset_position()
            self.find_new_instrument()
            return
        if not self.bought and self.prev_price and ltp > self.prev_price:
            if place_order(self.instrument_key, "BUY", ltp):
                self.bought = True
                self.entry = ltp
                self.stop = ltp - STOP_LOSS_OFFSET
                self.peak = ltp
                log(f"Position opened @ {ltp}, Stop Loss = {self.stop}")
        elif self.bought:
            if ltp > self.peak:
                self.peak = ltp
            trail_exit = TRAIL_OFFSET
            if ltp <= self.entry + TRAIL_THRESHOLD:
                trail_exit = TRAIL_INITIAL
            trail_stop = self.peak - trail_exit
            if ltp <= trail_stop or ltp <= self.stop:
                if place_order(self.instrument_key, "SELL", ltp):
                    log(f"Position closed @ {ltp}")
                self.reset_position()
        self.prev_price = ltp

def run_strategy():
    global day_pnl, capital_used, current_instrument_valid
    if not ACCESS_TOKEN:
        log(f"No {ENVIRONMENT} token provided")
        return 0.0, 0.0
    log(f"Starting trading: {TRADING_MODE.upper()} mode, {ENVIRONMENT.upper()} data")
    instrument_data = load_instrument_data()
    if instrument_data:
        instrument_key = instrument_data["instrument_key"]
        log(f"Loaded instrument: {instrument_key}")
    else:
        log("No cached instrument, fetching new one")
        instrument_key, _, _ = get_new_instrument()
        if not instrument_key:
            log("Failed to initialize: No instrument key")
            return 0.0, 0.0
    strategy = TradingStrategy(instrument_key)
    if not strategy.running:
        log("Failed to initialize strategy")
        return 0.0, 0.0
    log("Starting market monitoring")
    last_price_time = dt.now(ist)
    consecutive_errors = 0
    while is_market_open() and strategy.running:
        try:
            if not current_instrument_valid:
                log("Current instrument invalid, getting new one")
                strategy.find_new_instrument()
                if not strategy.running:
                    time.sleep(5)
                    continue
            current_ltp = get_ltp_rest(strategy.instrument_key)
            if current_ltp:
                strategy.process_price_update(current_ltp)
                consecutive_errors = 0
                last_price_time = dt.now(ist)
            else:
                consecutive_errors += 1
                log(f"Failed to get price (error #{consecutive_errors})")
                if consecutive_errors >= 3:
                    log("Too many errors, invalidating instrument")
                    current_instrument_valid = False
                    consecutive_errors = 0
            if (dt.now(ist) - last_price_time).total_seconds() > 60:
                log("No price updates for 60 seconds, invalidating instrument")
                current_instrument_valid = False
                last_price_time = dt.now(ist)
            time.sleep(POLLING_INTERVAL)
        except Exception as e:
            log(f"Error in strategy loop: {e}")
            time.sleep(10)
    if strategy.bought:
        log("Market closed - squaring off")
        exit_price = get_ltp_rest(strategy.instrument_key)
        if exit_price and place_order(strategy.instrument_key, "SELL", exit_price):
            log(f"Final trade: Exit @ ₹{exit_price}")
    return day_pnl, capital_used

def print_summary():
    log("=" * 60)
    log("TRADING SESSION SUMMARY")
    log("=" * 60)
    log(f"Total Day P&L: ₹{day_pnl:.2f}")
    log(f"Total Capital Used: ₹{capital_used:.2f}")
    if capital_used > 0:
        log(f"Net Return: {(day_pnl / capital_used * 100):.2f}%")
    else:
        log("Net Return: 0.00% (No trades)")
    if paper_trades:
        log(f"Total Paper Trades: {len(paper_trades)}")
        log(f"Trades logged in: {TRADES_LOG_FILE}")
    log("=" * 60)

async def main():
    log("Initializing trading system...")
    try:
        
        log("Dependencies verified")
    except ImportError as e:
        log(f"Missing dependency: {e}")
        return
    from threading import Thread
    flask_thread = Thread(target=lambda: app.run(port=5000, debug=False, use_reloader=False))
    flask_thread.daemon = True
    flask_thread.start()
    log("Flask server started on http://localhost:5000")
    chart_thread = Thread(target=fetch_chart_data, daemon=True)
    chart_thread.start()
    log("Chart data fetching started")
    strategy_thread = Thread(target=run_strategy, daemon=True)
    strategy_thread.start()
    log("Trading strategy started")
    await start_websocket_server()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("System stopped by user")
        print_summary()
    except Exception as e:
        log(f"Critical error: {e}")
        print_summary()