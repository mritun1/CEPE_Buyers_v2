# findstrikeprice.py
import requests
from key import SANDBOX_TOKEN, LIVE_TOKEN
from datetime import datetime as dt
import pytz

# -------------------------
# Globals
# -------------------------
BASE = "https://api-v2.upstox.com"
ENVIRONMENT = "live"  # change to "sandbox" if needed
ACCESS_TOKEN = LIVE_TOKEN if ENVIRONMENT == "live" else SANDBOX_TOKEN
HEADERS = {"Authorization": f"Bearer {ACCESS_TOKEN}", "Accept": "application/json"}
ist = pytz.timezone("Asia/Kolkata")


# -------------------------
# Logging helper
# -------------------------
def log(msg):
    timestamp = dt.now(ist).strftime("%Y-%m-%d %H:%M:%S")
    print(f"{timestamp} - {msg}")


# -------------------------
# Strike fetching
# -------------------------
def strike_prices(client, lower_bound=100, upper_bound=200):
    """
    Fetch nearest CE and PE strike prices for Nifty Bank index
    Returns: (expiry, ce_strike, pe_strike)
    """
    try:
        url = f"{BASE}/market-quote/instruments"
        params = {"exchange": "NSE_FO", "symbol": "BANKNIFTY"}
        resp = client.get(url, headers=HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        instruments = resp.json().get("data", [])

        if not instruments:
            log("No instruments found for BANKNIFTY")
            return "0", None, None

        # Filter only nearest expiry options
        expiries = sorted({inst["expiry"] for inst in instruments})
        nearest_expiry = expiries[0]

        ce_strikes = []
        pe_strikes = []
        for inst in instruments:
            if inst["expiry"] != nearest_expiry:
                continue
            strike = inst.get("strike_price")
            if not strike:
                continue
            if inst["option_type"] == "CE":
                ce_strikes.append(strike)
            elif inst["option_type"] == "PE":
                pe_strikes.append(strike)

        # Select nearest strike in the given LTP range
        ce_strike = min(ce_strikes, key=lambda x: abs(x - lower_bound))
        pe_strike = min(pe_strikes, key=lambda x: abs(x - upper_bound))

        return nearest_expiry, ce_strike, pe_strike

    except Exception as e:
        log(f"Error fetching strike prices: {e}")
        return "0", None, None


# -------------------------
# Instrument token fetching
# -------------------------
def get_instrument_token(client, expiry, strike, option_type="PE"):
    """
    Fetch instrument token for a specific BANKNIFTY option
    """
    try:
        url = f"{BASE}/market-quote/instruments"
        params = {"exchange": "NSE_FO", "symbol": "BANKNIFTY"}
        resp = client.get(url, headers=HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        instruments = resp.json().get("data", [])

        for inst in instruments:
            if (inst.get("expiry") == expiry and
                inst.get("strike_price") == strike and
                inst.get("option_type") == option_type):
                token = inst.get("instrument_token")
                log(f"Found instrument token: {token} for {option_type} strike {strike} expiry {expiry}")
                return token
        log(f"No instrument token found for {option_type} strike {strike} expiry {expiry}")
    except Exception as e:
        log(f"Error fetching instrument token: {e}")
    return None
