# backend.py
import os
import json
import time
import requests
import asyncio
import websockets
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from datetime import datetime as dt, time as dtime
import pytz
from threading import Thread
from findstrikeprice import strike_prices, get_instrument_token
from key import SANDBOX_TOKEN, LIVE_TOKEN

# -------------------------
# Configuration / Globals
# -------------------------
ist = pytz.timezone("Asia/Kolkata")

# Trading configuration (editable)
LTP_LOWER_BOUND = 100        # Choose minimum LTP
LTP_UPPER_BOUND = 200        # Choose maximum LTP
# UNDERLYING_INSTRUMENT = "NSE_INDEX|Nifty 50"
UNDERLYING_INSTRUMENT = "NSE_INDEX|Nifty Bank"  # Bank NIFTY
TRADING_MODE = "paper"       # "paper" or "live"
LOT_SIZE = 70                # Bank Nifty lot
STOP_LOSS_OFFSET = 20
TRAIL_OFFSET = 3
TRAIL_THRESHOLD = 2
TRAIL_INITIAL = 1
POLLING_INTERVAL = 2         # seconds between polls
ENVIRONMENT = "live"         # "live" or "sandbox" (for token selection)

# API / brokerage params
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

# Files
TRADES_LOG_FILE_TEMPLATE = "paper_trades_{mode}_{date}.json"

# App state
app = Flask(__name__, template_folder=".")
CORS(app, resources={r"/api/*": {"origins": "*"}})

log_messages = []
connected_clients = set()

# Separate CE / PE state
ce_running = False
pe_running = False

ce_trades = []
pe_trades = []
ce_day_pnl = 0.0
pe_day_pnl = 0.0
ce_capital_used = 0.0
pe_capital_used = 0.0

current_balance = 100000.0  # replace with actual balance retrieval if available

chart_data = {"labels": [], "values": []}

# -------------------------
# Helpers: File names
# -------------------------
def get_json_file(mode: str):
    mode = (mode or "PE").upper()
    return f"instrument_data_{mode}.json"

def get_trades_log_file(mode: str):
    mode = (mode or "PE").upper()
    return TRADES_LOG_FILE_TEMPLATE.format(mode=mode, date=dt.now().strftime("%d%b%Y").upper())

# -------------------------
# Logging & WebSocket broadcast
# -------------------------
def log(msg: str):
    timestamp = dt.now(ist).strftime("%Y-%m-%d %H:%M:%S")
    mode_ind = "🔴 LIVE" if USE_LIVE_TRADING else "📝 PAPER"
    message = f"{timestamp} [{mode_ind}] - {msg}"
    print(message)
    log_messages.append({"timestamp": timestamp, "message": message})
    # broadcast asynchronously
    if connected_clients:
        try:
            asyncio.create_task(broadcast_message({"type": "log", "data": message}))
        except RuntimeError:
            # if event loop not running yet, ignore
            pass

async def broadcast_message(message: dict):
    if not connected_clients:
        return
    payload = json.dumps(message)
    dead = []
    for ws in list(connected_clients):
        try:
            await ws.send(payload)
        except Exception as e:
            # mark dead sockets for cleanup
            dead.append(ws)
            print(f"WS send error: {e}")
    for ws in dead:
        try:
            connected_clients.remove(ws)
        except:
            pass

# -------------------------
# Chart / Market data fetcher
# -------------------------
def fetch_chart_data():
    global chart_data
    while True:
        try:
            url = f"{BASE}/market-quote/ohlc"
            params = {"instrument_key": UNDERLYING_INSTRUMENT, "interval": "1minute"}
            resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json().get("data", {}).get(UNDERLYING_INSTRUMENT, [])
            if data:
                labels = []
                values = []
                for d in data:
                    # timestamp ends with Z in ISO format - remove last char if present
                    ts = d.get("timestamp")
                    if ts and ts.endswith("Z"):
                        ts = ts[:-1]
                    try:
                        labels.append(dt.fromisoformat(ts).astimezone(ist).strftime("%H:%M"))
                    except Exception:
                        labels.append("")
                    values.append(float(d['ohlc']['close']))
                chart_data["labels"] = labels
                chart_data["values"] = values
                if connected_clients:
                    try:
                        asyncio.create_task(broadcast_message({"type": "chart", "data": chart_data}))
                    except RuntimeError:
                        pass
                log("Chart data updated")
            time.sleep(10)
        except Exception as e:
            log(f"Error fetching chart data: {e}")
            time.sleep(60)

# -------------------------
# Instrument caching
# -------------------------
def save_instrument_data(instrument_key, strike_ce, strike_pe, expiry, mode: str):
    file = get_json_file(mode)
    data = {
        "instrument_key": instrument_key,
        "strike_ce": strike_ce,
        "strike_pe": strike_pe,
        "expiry": expiry,
        "created_at": dt.now(ist).isoformat()
    }
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

def get_new_instrument(underlying_price=25000, mode: str = "PE"):
    """Returns (instrument_key, ce_strike, pe_strike) or (None, None, None)"""
    global current_instrument_valid
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
            current_instrument_valid = True
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
        # data has a key -> info mapping; extract the first item
        _, info = next(iter(data.items()))
        ltp = info.get("last_price")
        log(f"LTP for {key}: ₹{ltp}")
        return ltp
    except Exception as e:
        log(f"Error fetching LTP for {key}: {e}")
        return None

# -------------------------
# Charges calculator
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

# -------------------------
# Save trade (per-mode file)
# -------------------------
def save_trade_to_file(trade_data, mode: str):
    file_name = get_trades_log_file(mode)
    try:
        if os.path.exists(file_name):
            with open(file_name, "r") as f:
                trades = json.load(f)
        else:
            trades = []
        trades.append(trade_data)
        with open(file_name, "w") as f:
            json.dump(trades, f, indent=2)
        log(f"Saved trade to {file_name}")
    except Exception as e:
        log(f"Error saving trade to file {file_name}: {e}")

# -------------------------
# Place order (paper or live). Mode param indicates CE/PE
# -------------------------
def place_order(instrument_key: str, side: str, price: float = None, mode: str = "PE"):
    """
    Places a PAPER or LIVE order depending on USE_LIVE_TRADING.
    For PAPER: appends to ce_trades/pe_trades and computes PnL on SELL.
    For LIVE: sends API request to broker and mimics logging (still records trades locally).
    mode: "CE" or "PE" (used for JSON file and trade list)
    """
    global ce_trades, pe_trades, ce_day_pnl, pe_day_pnl, ce_capital_used, pe_capital_used, current_balance

    mode = (mode or "PE").upper()
    current_price = price or get_ltp_rest(instrument_key)
    if current_price is None:
        log(f"[{mode}] Failed to get price for {side} order")
        return False

    trade_data = {
        "timestamp": dt.now(ist).isoformat(),
        "instrument_key": instrument_key,
        "strike_price_mode": mode,
        "action": side.upper(),
        "quantity": LOT_SIZE,
        "price": round(current_price, 2),
        "order_type": "MARKET",
        "mode": "LIVE" if USE_LIVE_TRADING else "PAPER",
        "charges": {},
        "gross_profit": None,
        "net_profit": None,
        "day_pnl": None
    }

    # PAPER mode logic
    if not USE_LIVE_TRADING:
        # Append the trade
        if mode == "CE":
            target_list = ce_trades
        else:
            target_list = pe_trades

        target_list.append(trade_data)
        save_trade_to_file(trade_data, mode)

        log(f"[PAPER] {mode} {side} {LOT_SIZE} of {instrument_key} @ ₹{trade_data['price']}")

        # If this is a SELL, attempt to find the matching BUY and compute pnl
        if side.upper() == "SELL":
            last_buy = next((t for t in reversed(target_list) if t["instrument_key"] == instrument_key and t["action"] == "BUY"), None)
            # find previous BUY that doesn't already have net_profit set (optional improvement)
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
                if mode == "CE":
                    ce_day_pnl += net_profit
                    ce_capital_used += entry_price * LOT_SIZE
                else:
                    pe_day_pnl += net_profit
                    pe_capital_used += entry_price * LOT_SIZE
                current_balance += net_profit
                trade_data["day_pnl"] = (ce_day_pnl if mode == "CE" else pe_day_pnl)
                log(f"[PAPER] {mode} Trade Summary: Entry ₹{entry_price}, Exit ₹{exit_price}, Gross ₹{gross_profit}, Net ₹{net_profit}, Day PnL {(trade_data['day_pnl'])}")

        # broadcast trades + summary
        try:
            asyncio.create_task(broadcast_message({"type": "trades", "data": {"ce": ce_trades, "pe": pe_trades}}))
            asyncio.create_task(broadcast_message({"type": "summary", "data": get_full_summary()}))
        except RuntimeError:
            pass

        return True

    # LIVE trading: attempt to place order using Upstox API
    else:
        url = f"{BASE}/order/place"
        payload = {
            "instrument_key": instrument_key,
            "quantity": LOT_SIZE,
            "product": "I",
            "order_type": "MARKET",
            "transaction_type": side.upper(),
            "validity": "DAY"
        }
        try:
            resp = requests.post(url, headers=HEADERS, json=payload, timeout=10)
            resp.raise_for_status()
            log(f"[LIVE] {mode} {side} order placed successfully @ ₹{trade_data['price']}")
            # For simplicity record the live trade locally as well
            if mode == "CE":
                ce_trades.append(trade_data)
            else:
                pe_trades.append(trade_data)
            save_trade_to_file(trade_data, mode)

            # If SELL, compute PnL similar to PAPER (best-effort using stored last BUY)
            if side.upper() == "SELL":
                target_list = ce_trades if mode == "CE" else pe_trades
                last_buy = next((t for t in reversed(target_list) if t["instrument_key"] == instrument_key and t["action"] == "BUY"), None)
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
                    if mode == "CE":
                        ce_day_pnl += net_profit
                        ce_capital_used += entry_price * LOT_SIZE
                    else:
                        pe_day_pnl += net_profit
                        pe_capital_used += entry_price * LOT_SIZE
                    current_balance += net_profit
                    trade_data["day_pnl"] = (ce_day_pnl if mode == "CE" else pe_day_pnl)
                    log(f"[LIVE] {mode} Trade Summary: Entry ₹{entry_price}, Exit ₹{exit_price}, Net ₹{net_profit}, Day PnL {trade_data['day_pnl']}")

            # broadcast updates
            try:
                asyncio.create_task(broadcast_message({"type": "trades", "data": {"ce": ce_trades, "pe": pe_trades}}))
                asyncio.create_task(broadcast_message({"type": "summary", "data": get_full_summary()}))
            except RuntimeError:
                pass

            return True
        except requests.exceptions.HTTPError as e:
            log(f"[LIVE] HTTP error placing order: {e}")
            return False
        except Exception as e:
            log(f"[LIVE] Error placing order: {e}")
            return False

# -------------------------
# Summary assembly
# -------------------------
def get_full_summary():
    overall_day_pnl = ce_day_pnl + pe_day_pnl
    overall_capital_used = ce_capital_used + pe_capital_used
    all_trades = (ce_trades or []) + (pe_trades or [])
    overall_charges = sum(t.get("charges", {}).get("total", 0) for t in all_trades)
    return {
        "ce": {
            "day_pnl": ce_day_pnl,
            "capital_used": ce_capital_used,
            "trades": ce_trades
        },
        "pe": {
            "day_pnl": pe_day_pnl,
            "capital_used": pe_capital_used,
            "trades": pe_trades
        },
        "overall": {
            "day_pnl": overall_day_pnl,
            "capital_used": overall_capital_used,
            "charges": round(overall_charges, 2),
            "current_balance": round(current_balance, 2)
        }
    }

# -------------------------
# Strategy classes (one for CE and one for PE)
# -------------------------
class OptionStrategy:
    def __init__(self, mode: str):
        self.mode = (mode or "PE").upper()  # "CE" or "PE"
        self.instrument_key = None
        self.bought = False
        self.entry = None
        self.stop = None
        self.peak = None
        self.prev_price = None
        self.running = True
        # try load cached instrument
        inst = load_instrument_data(self.mode)
        if inst:
            self.instrument_key = inst.get("instrument_key")
            self.prev_price = get_ltp_rest(self.instrument_key) if self.instrument_key else None
            log(f"[{self.mode}] Loaded cached instrument {self.instrument_key} (prev_price={self.prev_price})")
        else:
            # will fetch when started
            log(f"[{self.mode}] No cached instrument found")

    def reset_position(self):
        self.bought = False
        self.entry = None
        self.stop = None
        self.peak = None

    def ensure_instrument(self):
        # find an instrument and ensure LTP in range
        retry_delay = 2
        while self.running:
            new_key, ce_strike, pe_strike = get_new_instrument(mode=self.mode)
            if not new_key:
                log(f"[{self.mode}] No instrument found, retrying in {retry_delay}s")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
                continue
            ltp = get_ltp_rest(new_key)
            if ltp and LTP_LOWER_BOUND <= ltp <= LTP_UPPER_BOUND:
                self.instrument_key = new_key
                self.prev_price = ltp
                log(f"[{self.mode}] Selected instrument {new_key} with LTP {ltp}")
                return
            else:
                log(f"[{self.mode}] Instrument {new_key} rejected (LTP={ltp}), retrying in {retry_delay}s")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

    def process_price_update(self, ltp):
        if ltp is None:
            return
        # check ltp range
        if ltp < LTP_LOWER_BOUND or ltp > LTP_UPPER_BOUND:
            log(f"[{self.mode}] LTP {ltp} outside range [{LTP_LOWER_BOUND}-{LTP_UPPER_BOUND}] - resetting")
            self.reset_position()
            self.ensure_instrument()
            return

        # entry logic: simple momentum (ltp > prev_price)
        if not self.bought and self.prev_price is not None and ltp > self.prev_price:
            ok = place_order(self.instrument_key, "BUY", ltp, mode=self.mode)
            if ok:
                self.bought = True
                self.entry = ltp
                self.stop = ltp - STOP_LOSS_OFFSET
                self.peak = ltp
                log(f"[{self.mode}] Position opened @ {ltp}, Stop Loss = {self.stop}")
        elif self.bought:
            # update peak
            if ltp > self.peak:
                self.peak = ltp
            # dynamic trail exit
            trail_exit = TRAIL_OFFSET
            if ltp <= self.entry + TRAIL_THRESHOLD:
                trail_exit = TRAIL_INITIAL
            trail_stop = self.peak - trail_exit
            if ltp <= trail_stop or ltp <= self.stop:
                ok = place_order(self.instrument_key, "SELL", ltp, mode=self.mode)
                if ok:
                    log(f"[{self.mode}] Position closed @ {ltp}")
                # reset after closing
                self.reset_position()
        self.prev_price = ltp

# -------------------------
# Runner loops for CE and PE
# -------------------------
def run_option_loop(mode: str):
    mode = (mode or "PE").upper()
    strategy = OptionStrategy(mode)
    # Ensure instrument is ready
    if not strategy.instrument_key:
        strategy.ensure_instrument()
    if not strategy.instrument_key:
        log(f"[{mode}] Failed to initialize strategy (no instrument)")
        return

    last_price_time = dt.now(ist)
    consecutive_errors = 0
    while (ce_running if mode == "CE" else pe_running) and strategy.running:
        try:
            ltp = get_ltp_rest(strategy.instrument_key)
            if ltp:
                strategy.process_price_update(ltp)
                consecutive_errors = 0
                last_price_time = dt.now(ist)
            else:
                consecutive_errors += 1
                log(f"[{mode}] Failed to get LTP (error #{consecutive_errors})")
                if consecutive_errors >= 3:
                    log(f"[{mode}] Too many LTP errors, invalidating instrument")
                    strategy.reset_position()
                    strategy.ensure_instrument()
                    consecutive_errors = 0
            # detect stale updates
            if (dt.now(ist) - last_price_time).total_seconds() > 60:
                log(f"[{mode}] No price updates for 60s, invalidating instrument")
                strategy.reset_position()
                strategy.ensure_instrument()
                last_price_time = dt.now(ist)
            time.sleep(POLLING_INTERVAL)
        except Exception as e:
            log(f"[{mode}] Error in loop: {e}")
            time.sleep(5)
    # On stop, square off if bought
    if strategy.bought:
        exit_price = get_ltp_rest(strategy.instrument_key)
        if exit_price:
            place_order(strategy.instrument_key, "SELL", exit_price, mode=mode)
            log(f"[{mode}] Squared off on stop with exit @ {exit_price}")
    log(f"[{mode}] Loop ended")

# -------------------------
# REST API endpoints
# -------------------------
@app.route('/')
def index():
    # optional: return a minimal index if frontend not present
    try:
        return render_template("index.html")
    except:
        return "<h3>Trading backend</h3>", 200

@app.route('/api/logs', methods=['GET'])
def api_logs():
    return jsonify(log_messages[-50:])

@app.route('/api/trades', methods=['GET'])
def api_trades():
    return jsonify({"ce": ce_trades, "pe": pe_trades})

@app.route('/api/summary', methods=['GET'])
def api_summary():
    return jsonify(get_full_summary())

@app.route('/api/chart', methods=['GET'])
def api_chart():
    return jsonify(chart_data)

# Start/Stop endpoints for CE / PE
@app.route('/api/start_ce', methods=['POST'])
def api_start_ce():
    global ce_running
    if not ce_running:
        ce_running = True
        Thread(target=run_option_loop, args=("CE",), daemon=True).start()
        log("CE trading started")
        return jsonify({"status": "CE started"})
    else:
        return jsonify({"status": "CE already running"})

@app.route('/api/stop_ce', methods=['POST'])
def api_stop_ce():
    global ce_running
    if ce_running:
        ce_running = False
        log("CE trading stop requested")
        return jsonify({"status": "CE stop requested"})
    return jsonify({"status": "CE not running"})

@app.route('/api/start_pe', methods=['POST'])
def api_start_pe():
    global pe_running
    if not pe_running:
        pe_running = True
        Thread(target=run_option_loop, args=("PE",), daemon=True).start()
        log("PE trading started")
        return jsonify({"status": "PE started"})
    else:
        return jsonify({"status": "PE already running"})

@app.route('/api/stop_pe', methods=['POST'])
def api_stop_pe():
    global pe_running
    if pe_running:
        pe_running = False
        log("PE trading stop requested")
        return jsonify({"status": "PE stop requested"})
    return jsonify({"status": "PE not running"})

# endpoint to fetch saved instrument json for a mode
@app.route('/api/instrument/<mode>', methods=['GET'])
def api_instrument(mode):
    inst = load_instrument_data(mode)
    if inst:
        return jsonify(inst)
    return jsonify({"error": "no instrument cached"}), 404

@app.route('/api/market_status', methods=['GET'])
def get_market_status():
    now = dt.now(ist)
    market_open_time = dtime(9, 15)
    market_close_time = dtime(15, 30)
    is_open = market_open_time <= now.time() <= market_close_time
    status = "Open" if is_open else "Closed"
    color = "#34d399" if is_open else "#f87171"
    return jsonify({"status": status, "color": color})

@app.route('/api/trade_status', methods=['GET'])
def trade_status():
    ce_active = any(t for t in paper_trades if t.get("strike_price_mode")=="CE" and t["action"]=="BUY")
    pe_active = any(t for t in paper_trades if t.get("strike_price_mode")=="PE" and t["action"]=="BUY")
    return jsonify({
        "CE": "Trading" if ce_active else "Idle",
        "PE": "Trading" if pe_active else "Idle"
    })

@app.route('/api/instrument/<mode>', methods=['GET'])
def get_instrument(mode):
    json_file = f"instrument_data_{mode}.json"
    if os.path.exists(json_file):
        with open(json_file, "r") as f:
            data = json.load(f)
        return jsonify(data)
    return jsonify({"error": "No instrument data"}), 404



# -------------------------
# WebSocket server
# -------------------------
async def websocket_handler(ws):
    # note: websockets server calls handler with a single param in modern versions
    connected_clients.add(ws)
    log("WebSocket connection established")
    try:
        # send initial snapshot
        for l in log_messages[-50:]:
            await ws.send(json.dumps({"type": "log", "data": l["message"]}))
        await ws.send(json.dumps({"type": "trades", "data": {"ce": ce_trades, "pe": pe_trades}}))
        await ws.send(json.dumps({"type": "summary", "data": get_full_summary()}))
        await ws.send(json.dumps({"type": "chart", "data": chart_data}))
        async for message in ws:
            log(f"WS message from client: {message}")
    except websockets.exceptions.ConnectionClosed:
        log("WebSocket connection closed by client")
    except Exception as e:
        log(f"WebSocket error: {e}")
    finally:
        try:
            connected_clients.remove(ws)
        except:
            pass
        log("WebSocket client removed")

async def start_websocket_server():
    log("Starting WebSocket server on ws://localhost:8765")
    server = await websockets.serve(websocket_handler, "localhost", 8765)
    await server.wait_closed()

# -------------------------
# Main
# -------------------------
def start_flask():
    # run flask without reloader in a thread
    app.run(port=5000, debug=False, use_reloader=False)

async def main_async():
    # start Flask in thread
    Thread(target=start_flask, daemon=True).start()
    log("Flask server started on http://localhost:5000")

    # start chart fetching thread
    Thread(target=fetch_chart_data, daemon=True).start()
    log("Chart data fetcher started")

    # Start websocket server (blocks until cancelled)
    await start_websocket_server()

if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        log("System stopped by user")
    except Exception as e:
        log(f"Critical error: {e}")
