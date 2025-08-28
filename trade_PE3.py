import time
import websocket
import json
import requests
import httpx
import os
from datetime import datetime as dt, time as dtime
from findstrikeprice import strike_prices, get_instrument_token
from key import SANDBOX_TOKEN, LIVE_TOKEN
from datetime import datetime

LTP_LOWER_BOUND = 100 #Choose minmum LTP
LTP_UPPER_BOUND = 200 #Choose maximum LTP
# UNDERLYING_INSTRUMENT = "NSE_INDEX|Nifty 50" #NIFTY 50
UNDERLYING_INSTRUMENT = "NSE_INDEX|Nifty Bank" #Bank NIFTY
STRIKE_PRICE_MODE = "PE"
TRADING_MODE = "paper"  # Set to "paper" for simulated trades or "live" for real trades
# LOT_SIZE = 75  # Nifty 50 Lot size - 75
LOT_SIZE = 70 # Bank Nifty Lot size - 35
STOP_LOSS_OFFSET = 20 #STOP LOSS
TRAIL_OFFSET = 3 #TRAIL EXIT

# CONFIG - HYBRID MODE: Live data + Paper trading
ENVIRONMENT = "live"  # Use "live" for real market data
JSON_FILE = "instrument_data_"+STRIKE_PRICE_MODE+".json"
ACCESS_TOKEN = LIVE_TOKEN if ENVIRONMENT == "live" else SANDBOX_TOKEN
USE_LIVE_TRADING = TRADING_MODE == "live"

TRAIL_THRESHOLD = 2
TRAIL_INITIAL = 1
POLLING_INTERVAL = 2  # seconds between REST API LTP checks

# Base URLs - Always use live for market data
BASE = "https://api-v2.upstox.com"

# Headers with full token for actual API calls (not truncated)
HEADERS = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Accept": "application/json"}
DISPLAY_HEADERS = {"Authorization": f"Bearer {ACCESS_TOKEN[:5]}...{ACCESS_TOKEN[-5:]}", "Accept": "application/json"}

TRADES_LOG_FILE = "paper_trades.json"  # Log paper trades for analysis

# CONSTANTS: Fees for Equity Options (Nifty weekly)
BROKERAGE_PER_ORDER = 20
STT_RATE = 0.000625
TRANSACTION_RATE = 0.0003503
STAMP_DUTY_RATE = 0.00003
SEBI_TURNOVER = 10 / 1e7
IPFT_RATE = 0.50 / 1e5
GST_RATE = 0.18

# Global variables
day_pnl = 0.0
overall_pnl = 0.0
capital_used = 0.0
current_instrument_valid = True
paper_trades = []  # Store paper trades for analysis

today_str = datetime.now().strftime("%d%b%Y").upper()

def log(msg):
    timestamp = dt.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    mode_indicator = "🔴 LIVE" if USE_LIVE_TRADING else "📝 PAPER"
    print(f"{timestamp} [{mode_indicator}] - {msg}")

def is_market_open(now=None):
    now = now or dt.now()
    return dtime(9, 20) <= now.time() <= dtime(15, 10)

# def save_paper_trade(trade_data):
#     """Save paper trade to log file for analysis"""
#     try:
#         if os.path.exists(TRADES_LOG_FILE):
#             with open(TRADES_LOG_FILE, 'r') as f:
#                 trades = json.load(f)
#         else:
#             trades = []
        
#         trades.append(trade_data)
        
#         with open(TRADES_LOG_FILE, 'w') as f:
#             json.dump(trades, f, indent=2, default=str)
        
#         log(f"Paper trade logged: {trade_data['action']}")
#     except Exception as e:
#         log(f"Error saving paper trade: {e}")


def save_trade(trade_data, mode="PAPER"):
    """Save trades to separate JSON files"""
    file_name = "paper_trades_"+STRIKE_PRICE_MODE+"_"+today_str+".json" if mode == "PAPER" else "live_trades_"+STRIKE_PRICE_MODE+"_"+today_str+".json"
    try:
        if os.path.exists(file_name):
            with open(file_name, "r") as f:
                trades = json.load(f)
        else:
            trades = []

        trades.append(trade_data)

        with open(file_name, "w") as f:
            json.dump(trades, f, indent=2)
    except Exception as e:
        log(f"⚠️ Error saving trade: {e}")

def get_websocket_url():
    """Get the proper WebSocket URL from Upstox API"""
    try:
        url = f"{BASE}/feed/market-data-feed/authorize"
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        if data.get("status") == "success":
            ws_url = data.get("data", {}).get("authorized_redirect_uri")
            if ws_url:
                log(f"✅ WebSocket URL obtained: {ws_url[:50]}...")
                return ws_url
        
        log(f"❌ Failed to get WebSocket URL: {data}")
        return None
        
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            log(f"❌ 401 Unauthorized: Check your token. Response: {e.response.json()}")
        else:
            log(f"❌ HTTP error getting WebSocket URL: {e}")
        return None
    except Exception as e:
        log(f"❌ Error getting WebSocket URL: {e}")
        return None

def get_ltp_rest(key):
    """Get LTP using REST API as fallback"""
    try:
        url = f"{BASE}/market-quote/ltp"
        params = {"instrument_key": key}
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        if not data:
            return None
        _, info = next(iter(data.items()))
        ltp = info.get("last_price")
        return ltp
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            log(f"401 Unauthorized: Invalid or expired {ENVIRONMENT} token.")
        else:
            log(f"HTTP error fetching LTP: {e}")
        return None
    except Exception as e:
        log(f"Error fetching LTP: {e}")
        return None

# def place_order(key, side, price=None):
#     """Place order - either paper or live based on configuration"""
#     current_price = price or get_ltp_rest(key)
#     if not current_price:
#         log(f"[{'LIVE' if USE_LIVE_TRADING else 'PAPER'}] Failed to get price for {side} order")
#         return False

#     mode = "LIVE" if USE_LIVE_TRADING else "PAPER"
#     trade_data = {
#         "timestamp": dt.now().isoformat(),
#         "instrument_key": key,
#         "strike_price_mode": STRIKE_PRICE_MODE,
#         "action": side.upper(),
#         "quantity": LOT_SIZE,
#         "price": round(current_price, 2),
#         "order_type": "MARKET",
#         "mode": mode,
#         "charges": {},
#         "gross_profit": None,
#         "net_profit": None
#     }

#     # === PAPER TRADING ===
#     if not USE_LIVE_TRADING:
#         paper_trades.append(trade_data)
#         log(f"[PAPER] {side} {LOT_SIZE} of {key} @ ₹{trade_data['price']}")

#         if side.upper() == "SELL":
#             last_buy = next((t for t in reversed(paper_trades) 
#                              if t["instrument_key"] == key and t["action"] == "BUY"), None)
#             if last_buy:
#                 entry_price = last_buy["price"]
#                 exit_price = trade_data["price"]
#                 gross_profit = round((exit_price - entry_price) * LOT_SIZE, 2)

#                 charges = calculate_charges(entry_price, exit_price, LOT_SIZE)
#                 # round each charge
#                 charges = {k: round(v, 2) for k, v in charges.items()}

#                 net_profit = round(gross_profit - charges["total"], 2)

#                 trade_data["gross_profit"] = gross_profit
#                 trade_data["net_profit"] = net_profit
#                 trade_data["charges"] = charges

#                 log(f"📊 Trade Summary: Entry ₹{entry_price}, Exit ₹{exit_price}, Qty {LOT_SIZE}")
#                 log(f"💰 Gross PnL = ₹{gross_profit}")
#                 log(f"🧾 Charges -> {charges}")
#                 log(f"✅ Net PnL = ₹{net_profit}")

#         save_trade(trade_data, "PAPER")
#         return True

#     # === LIVE TRADING ===
#     else:
#         url = f"{BASE}/order/place"
#         payload = {
#             "instrument_key": key,
#             "quantity": LOT_SIZE,
#             "product": "I",  # Intraday
#             "order_type": "MARKET",
#             "transaction_type": side,
#             "validity": "DAY"
#         }
#         try:
#             resp = requests.post(url, headers=HEADERS, json=payload, timeout=10)
#             resp.raise_for_status()
#             log(f"[LIVE] {side} order placed successfully @ ₹{trade_data['price']}")

#             if side.upper() == "SELL":
#                 last_buy = next((t for t in reversed(paper_trades) 
#                                  if t["instrument_key"] == key and t["action"] == "BUY"), None)
#                 if last_buy:
#                     entry_price = last_buy["price"]
#                     exit_price = trade_data["price"]
#                     gross_profit = round((exit_price - entry_price) * LOT_SIZE, 2)

#                     charges = calculate_charges(entry_price, exit_price, LOT_SIZE)
#                     charges = {k: round(v, 2) for k, v in charges.items()}
#                     net_profit = round(gross_profit - charges["total"], 2)

#                     trade_data["gross_profit"] = gross_profit
#                     trade_data["net_profit"] = net_profit
#                     trade_data["charges"] = charges

#                     log(f"📊 (LIVE) Trade Summary: Entry ₹{entry_price}, Exit ₹{exit_price}, Qty {LOT_SIZE}")
#                     log(f"💰 Gross PnL = ₹{gross_profit}")
#                     log(f"🧾 Charges -> {charges}")
#                     log(f"✅ Net PnL = ₹{net_profit}")

#             save_trade(trade_data, "LIVE")
#             return True

#         except requests.exceptions.HTTPError as e:
#             if e.response.status_code == 401:
#                 log(f"401 Unauthorized: Invalid token for live trading")
#             else:
#                 log(f"HTTP error placing order: {e}")
#             return False
#         except Exception as e:
#             log(f"Error placing order: {e}")
#             return False

def reset_daily_pnl():
    """Call this at midnight or start of trading day"""
    global overall_pnl
    overall_pnl = 0.0

def place_order(key, side, price=None):
    """Place order - either paper or live based on configuration"""
    global overall_pnl

    current_price = price or get_ltp_rest(key)
    if not current_price:
        log(f"[{'LIVE' if USE_LIVE_TRADING else 'PAPER'}] Failed to get price for {side} order")
        return False

    mode = "LIVE" if USE_LIVE_TRADING else "PAPER"
    trade_data = {
        "timestamp": dt.now().isoformat(),
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
        "day_pnl": None   # 👈 NEW FIELD for running day profit
    }

    # === PAPER TRADING ===
    if not USE_LIVE_TRADING:
        paper_trades.append(trade_data)
        log(f"[PAPER] {side} {LOT_SIZE} of {key} @ ₹{trade_data['price']}")

        if side.upper() == "SELL":
            last_buy = next((t for t in reversed(paper_trades) 
                             if t["instrument_key"] == key and t["action"] == "BUY"), None)
            if last_buy:
                entry_price = last_buy["price"]
                exit_price = trade_data["price"]
                gross_profit = round((exit_price - entry_price) * LOT_SIZE, 2)

                charges = calculate_charges(entry_price, exit_price, LOT_SIZE)
                charges = {k: round(v, 2) for k, v in charges.items()}
                net_profit = round(gross_profit - charges["total"], 2)

                # Update trade data
                trade_data["gross_profit"] = gross_profit
                trade_data["net_profit"] = net_profit
                trade_data["charges"] = charges

                # === Update daily counter ===
                overall_pnl += net_profit
                trade_data["day_pnl"] = overall_pnl

                # Log summary
                log(f"📊 Trade Summary: Entry ₹{entry_price}, Exit ₹{exit_price}, Qty {LOT_SIZE}")
                log(f"💰 Gross PnL = ₹{gross_profit}")
                log(f"🧾 Charges -> {charges}")
                log(f"✅ Net PnL = ₹{net_profit} | 📈 Day Total = ₹{overall_pnl}")

        save_trade(trade_data, "PAPER")
        return True

    # === LIVE TRADING ===
    else:
        url = f"{BASE}/order/place"
        payload = {
            "instrument_key": key,
            "quantity": LOT_SIZE,
            "product": "I",  # Intraday
            "order_type": "MARKET",
            "transaction_type": side,
            "validity": "DAY"
        }
        try:
            resp = requests.post(url, headers=HEADERS, json=payload, timeout=10)
            resp.raise_for_status()
            log(f"[LIVE] {side} order placed successfully @ ₹{trade_data['price']}")

            if side.upper() == "SELL":
                last_buy = next((t for t in reversed(paper_trades) 
                                 if t["instrument_key"] == key and t["action"] == "BUY"), None)
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

                    # === Update daily counter ===
                    overall_pnl += net_profit
                    trade_data["day_pnl"] = overall_pnl

                    # Log summary
                    log(f"📊 (LIVE) Trade Summary: Entry ₹{entry_price}, Exit ₹{exit_price}, Qty {LOT_SIZE}")
                    log(f"💰 Gross PnL = ₹{gross_profit}")
                    log(f"🧾 Charges -> {charges}")
                    log(f"✅ Net PnL = ₹{net_profit} | 📈 Day Total = ₹{overall_pnl}")

            save_trade(trade_data, "LIVE")
            return True

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                log(f"401 Unauthorized: Invalid token for live trading")
            else:
                log(f"HTTP error placing order: {e}")
            return False
        except Exception as e:
            log(f"Error placing order: {e}")
            return False


# def calculate_charges(price, side):
#     total_value = price * LOT_SIZE
#     brokerage = BROKERAGE_PER_ORDER
#     transaction = total_value * TRANSACTION_RATE
#     ipft = total_value * IPFT_RATE
#     gst = GST_RATE * (brokerage + transaction + ipft)
#     sebi = SEBI_TURNOVER * total_value
#     stamp = total_value * STAMP_DUTY_RATE if side == "BUY" else 0
#     stt = total_value * STT_RATE if side == "SELL" else 0
#     return brokerage + transaction + gst + sebi + stamp + stt

def calculate_charges(entry_price, exit_price, qty):
    """Calculate total charges for one round-trip trade"""
    turnover = (entry_price + exit_price) * qty

    # Charges
    brokerage = 2 * BROKERAGE_PER_ORDER  # both buy + sell
    stt = exit_price * qty * STT_RATE    # only on sell side
    txn_charges = turnover * TRANSACTION_RATE
    stamp_duty = entry_price * qty * STAMP_DUTY_RATE  # only on buy side
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
        "created_at": dt.now().isoformat()
    }
    try:
        with open(JSON_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        log(f"Saved instrument data to {JSON_FILE}")
    except Exception as e:
        log(f"Error saving JSON: {e}")

def get_new_instrument(underlying_price=25000):
    global current_instrument_valid
    with httpx.Client() as client:
        for attempt in range(3):
            try:
                log(f"Attempt {attempt + 1} to fetch new instrument")
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
                log(f"🔄 Generated new instrument: {instrument_key} ({STRIKE_PRICE_MODE} strike: {pe_strike}, expiry: {expiry})")
                current_instrument_valid = True
                return instrument_key, ce_strike, pe_strike
            except Exception as e:
                log(f"Error in get_new_instrument (attempt {attempt + 1}): {e}")
                if attempt < 2:
                    time.sleep(2)
                continue
        log(f"Failed to get new instrument after 3 attempts")
        return None, None, None

# class TradingStrategy:
#     def __init__(self, instrument_key):
#         self.instrument_key = instrument_key
#         self.prev_price = None
#         self.bought = False
#         self.entry = None
#         self.peak = None
#         self.stop = None
#         self.running = True
        
#         # Get initial price
#         self.prev_price = get_ltp_rest(instrument_key)
#         if self.prev_price:
#             log(f"📊 Initial LTP: ₹{self.prev_price}")
#         else:
#             log("❌ Could not get initial price")
#             self.running = False

#     def process_price_update(self, ltp):
#         global day_pnl, capital_used, current_instrument_valid
        
#         if not ltp:
#             log("❌ Invalid price received")
#             return
        
#         log(f"📊 LTP: ₹{ltp}, Prev: ₹{self.prev_price}")

#         # Check if LTP is within our trading range
#         if ltp < LTP_LOWER_BOUND or ltp > LTP_UPPER_BOUND:
#             log(f"❌ LTP {ltp} outside range [{LTP_LOWER_BOUND}-{LTP_UPPER_BOUND}], invalidating instrument")
#             current_instrument_valid = False
#             self.running = False
#             return

#         # Buy signal: price moving up from previous
#         if not self.bought and self.prev_price and ltp > self.prev_price:
#             log(f"🚀 Buy signal detected: LTP {ltp} > Prev {self.prev_price}")
#             if place_order(self.instrument_key, "BUY", ltp):
#                 self.entry = ltp
#                 capital_used += self.entry * LOT_SIZE
#                 self.stop = self.entry - STOP_LOSS_OFFSET
#                 self.peak = self.entry
#                 self.bought = True
#                 log(f"✅ Position opened @ ₹{self.entry}, Stop Loss = ₹{self.stop}")

#         elif self.bought:
#             # Update peak if price is rising
#             if ltp > self.peak:
#                 old_peak = self.peak
#                 self.peak = ltp
#                 log(f"📈 New peak: ₹{self.peak} (was ₹{old_peak})")

#             # Calculate running P&L
#             running_pnl = (ltp - self.entry) * LOT_SIZE
#             log(f"💰 Running PnL (gross): ₹{running_pnl:.2f}")

#             # Dynamic trailing stop
#             trail_exit = TRAIL_OFFSET
#             if ltp <= self.entry + TRAIL_THRESHOLD:
#                 trail_exit = TRAIL_INITIAL

#             # Exit conditions
#             trail_stop = self.peak - trail_exit
#             if ltp <= trail_stop or ltp <= self.stop:
#                 exit_reason = "Trail stop" if ltp <= trail_stop else "Stop loss"
#                 log(f"🛑 Exit signal: {exit_reason} (LTP: ₹{ltp}, Trail: ₹{trail_stop}, SL: ₹{self.stop})")
                
#                 if place_order(self.instrument_key, "SELL", ltp):
#                     exit_price = ltp
#                     gross_pnl = (exit_price - self.entry) * LOT_SIZE
#                     charges = calculate_charges(self.entry, "BUY") + calculate_charges(exit_price, "SELL")
#                     net_pnl = gross_pnl - charges
#                     day_pnl += net_pnl
                    
#                     log(f"🏁 Position closed @ ₹{exit_price}")
#                     log(f"📊 Trade P&L: Gross = ₹{gross_pnl:.2f}, Charges = ₹{charges:.2f}, Net = ₹{net_pnl:.2f}")
#                     log(f"📈 Day P&L: ₹{day_pnl:.2f}")
                    
#                     # Reset for next trade
#                     self.bought = False
#                     self.entry = None
#                     self.peak = None
#                     self.stop = None
                    
#                     log("🔄 Ready for next trade opportunity")

#         # Update previous price
#         self.prev_price = ltp

class TradingStrategy:
    def __init__(self, instrument_key):
        self.instrument_key = instrument_key
        self.reset_position()
        self.running = True  # ✅ re-added to match rest of code

    def reset_position(self):
        self.bought = False
        self.entry = None
        self.stop = None
        self.peak = None
        self.prev_price = None

    def find_new_instrument(self):
        retry_delay = 2
        while True:
            log("🔄 Trying to find a new instrument ...")
            new_key, ce_strike, pe_strike = get_new_instrument()   # ✅ use your existing helper

            if not new_key:
                log(f"⚠️ No instrument found, retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
                continue

            # ✅ Validate immediately
            ltp = get_ltp_rest(new_key)
            if ltp and LTP_LOWER_BOUND <= ltp <= LTP_UPPER_BOUND:
                log(f"✅ Found valid instrument {new_key} with LTP {ltp}")
                self.instrument_key = new_key
                self.prev_price = ltp
                return
            else:
                log(f"❌ Instrument {new_key} rejected (LTP={ltp}), retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

    def process_price_update(self, ltp):
        if ltp < LTP_LOWER_BOUND or ltp > LTP_UPPER_BOUND:
            log(f"❌ LTP {ltp} outside range [{LTP_LOWER_BOUND}-{LTP_UPPER_BOUND}], invalidating instrument ..")
            self.reset_position()
            self.find_new_instrument()
            return

        # Buy signal
        if not self.bought and self.prev_price and ltp > self.prev_price:
            if place_order(self.instrument_key, "BUY", ltp):
                self.bought = True
                self.entry = ltp
                self.stop = ltp - STOP_LOSS_OFFSET
                self.peak = ltp
                log(f"✅ Position opened @ {ltp}, Stop Loss = {self.stop}")

        # Manage active trade
        elif self.bought:
            if ltp > self.peak:
                self.peak = ltp
            trail_exit = TRAIL_OFFSET
            if ltp <= self.entry + TRAIL_THRESHOLD:
                trail_exit = TRAIL_INITIAL
            trail_stop = self.peak - trail_exit
            if ltp <= trail_stop or ltp <= self.stop:
                if place_order(self.instrument_key, "SELL", ltp):
                    log(f"🏁 Position closed @ {ltp}")
                self.reset_position()

        self.prev_price = ltp

def run_rest_api_strategy():
    """Run strategy using REST API polling instead of WebSocket"""
    global day_pnl, capital_used, current_instrument_valid
    
    log("🔄 Using REST API polling mode (WebSocket unavailable)")
    
    # Load or create instrument
    instrument_data = load_instrument_data()
    if instrument_data:
        instrument_key = instrument_data["instrument_key"]
        log(f"📂 Loaded instrument from cache: {instrument_key}")
    else:
        log("🔍 No cached instrument, fetching new one...")
        instrument_key, _, _ = get_new_instrument()
        if not instrument_key:
            log(f"❌ Failed to initialize: No instrument key available")
            return 0.0, 0.0
    
    # Initialize strategy
    strategy = TradingStrategy(instrument_key)
    if not strategy.running:
        log("❌ Failed to initialize strategy")
        return 0.0, 0.0
    
    log("⏰ Starting market monitoring with REST API polling...")
    
    # Main trading loop
    last_price_time = dt.now()
    consecutive_errors = 0
    
    while is_market_open() and strategy.running:
        try:
            if not current_instrument_valid:
                log("🔄 Current instrument invalid, getting new one...")
                new_instrument_key, _, _ = get_new_instrument()
                if not new_instrument_key:
                    log("⚠️ Failed to get new instrument, retrying in 5 seconds...")
                    time.sleep(5)
                    continue
                
                # Switch to new instrument
                strategy = TradingStrategy(new_instrument_key)
                if not strategy.running:
                    log("❌ Failed to initialize new strategy")
                    time.sleep(5)
                    continue
                
                current_instrument_valid = True
            
            # Get current price
            current_ltp = get_ltp_rest(strategy.instrument_key)
            
            if current_ltp:
                strategy.process_price_update(current_ltp)
                consecutive_errors = 0
                last_price_time = dt.now()
            else:
                consecutive_errors += 1
                log(f"⚠️ Failed to get price (error #{consecutive_errors})")
                
                if consecutive_errors >= 3:
                    log("❌ Too many consecutive errors, invalidating instrument")
                    current_instrument_valid = False
                    consecutive_errors = 0
            
            # Check if we haven't received price updates for too long
            if (dt.now() - last_price_time).total_seconds() > 60:
                log("⚠️ No price updates for 60 seconds, invalidating instrument")
                current_instrument_valid = False
                last_price_time = dt.now()
            
            time.sleep(POLLING_INTERVAL)
            
        except KeyboardInterrupt:
            log("👋 Manual interruption received")
            break
        except Exception as e:
            log(f"❌ Error in main loop: {e}")
            time.sleep(1)

    # Market closed - square off any open position
    if strategy.bought:
        log("🏁 Market closed - squaring off open position")
        exit_price = get_ltp_rest(strategy.instrument_key)
        if exit_price and place_order(strategy.instrument_key, "SELL", exit_price):
            gross_pnl = (exit_price - strategy.entry) * LOT_SIZE
            charges = calculate_charges(strategy.entry, "BUY") + calculate_charges(exit_price, "SELL")
            net_pnl = gross_pnl - charges
            day_pnl += net_pnl
            log(f"📊 Final trade: Exit @ ₹{exit_price}, Net PnL = ₹{net_pnl:.2f}")

    return day_pnl, capital_used

def run_strategy():
    global day_pnl, capital_used, current_instrument_valid
    
    # Validate token
    if not ACCESS_TOKEN:
        log(f"❌ No {ENVIRONMENT} token provided. Set LIVE_TOKEN in key.py")
        return 0.0, 0.0

    log(f"🚀 Starting hybrid trading system:")
    log(f"   📊 Data source: {ENVIRONMENT.upper()}")
    log(f"   💼 Trading mode: {TRADING_MODE.upper()}")
    log(f"   🔑 Token: {ACCESS_TOKEN[:10]}...{ACCESS_TOKEN[-5:]}")
    
    # Try WebSocket first, fallback to REST API
    ws_url = get_websocket_url()
    if ws_url:
        log("⚠️ WebSocket URL obtained but WebSocket implementation is complex with Upstox V3")
        log("🔄 Falling back to REST API polling for reliable operation")
    else:
        log("⚠️ Could not get WebSocket URL, using REST API polling")
    
    return run_rest_api_strategy()

def print_summary():
    """Print trading session summary"""
    log("=" * 60)
    log("📊 TRADING SESSION SUMMARY")
    log("=" * 60)
    log(f"💰 Total Day P&L: ₹{day_pnl:.2f}")
    log(f"💼 Total Capital Used: ₹{capital_used:.2f}")
    
    if capital_used > 0:
        return_pct = (day_pnl / capital_used) * 100
        log(f"📈 Net Return: {return_pct:.2f}%")
    else:
        log("📈 Net Return: 0.00% (No trades executed)")
    
    if paper_trades:
        log(f"📝 Total Paper Trades: {len(paper_trades)}")
        log(f"📂 Trades logged in: {TRADES_LOG_FILE}")
    
    log("=" * 60)

if __name__ == "__main__":
    try:
        log("🚀 Initializing hybrid trading system...")
        pnl, used = run_strategy()
        print_summary()
        
    except KeyboardInterrupt:
        log("👋 System stopped by user")
        print_summary()
    except Exception as e:
        log(f"❌ Critical error: {e}")
        print_summary()