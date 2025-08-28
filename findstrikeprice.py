import httpx
from datetime import datetime
from typing import Tuple
from datetime import datetime as dt, time as dtime

from key import SANDBOX_TOKEN, LIVE_TOKEN

# Always use live for market data in hybrid mode
ENVIRONMENT = "live"  # Changed to live for real market data
ACCESS_TOKEN = LIVE_TOKEN  # Use live token for market data
BASE_URL = "https://api-v2.upstox.com"  # Live API endpoint
INSTRUMENT_KEY = "NSE_INDEX|Nifty 50"

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",  # Use full token for API calls
    "Accept": "application/json"
}

def log(msg):
    print(f"{dt.now()} - {msg}")

def get_expiries(client: httpx.Client) -> list:
    url = f"{BASE_URL}/option/contract"
    try:
        log(f"Fetching expiries with live token: {ACCESS_TOKEN[:5]}...{ACCESS_TOKEN[-5:]}")
        resp = client.get(url, headers=HEADERS, params={"instrument_key": INSTRUMENT_KEY}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("status") != "success":
            log(f"API returned error: {data}")
            return []
            
        expiries = []
        for item in data.get("data", []):
            expiry = item.get("expiry") or item.get("expiry_date")
            if expiry:
                expiries.append(expiry)
        
        if not expiries:
            log("No expiries found in response")
            return []
            
        # Sort expiries by date
        sorted_expiries = sorted(expiries, key=lambda d: datetime.fromisoformat(d.replace("Z", "+05:30")))
        log(f"Found {len(sorted_expiries)} expiries, nearest: {sorted_expiries[0] if sorted_expiries else 'None'}")
        return sorted_expiries
        
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            print(f"401 Unauthorized: Invalid or expired live token. Response: {e.response.json()}. Regenerate at https://api-v2.upstox.com/developer/apps")
        else:
            print(f"HTTP error fetching expiries: {e}. Response: {e.response.json()}")
        return []
    except Exception as e:
        print(f"Error fetching expiries: {e}. Check network or API endpoint ({BASE_URL})")
        return []

def get_option_chain(client: httpx.Client, expiry_date: str) -> list:
    url = f"{BASE_URL}/option/chain"
    try:
        log(f"Fetching option chain for expiry: {expiry_date}")
        resp = client.get(
            url,
            headers=HEADERS,
            params={"instrument_key": INSTRUMENT_KEY, "expiry_date": expiry_date},
            timeout=15  # Increased timeout for option chain
        )
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("status") != "success":
            print(f"Error fetching option chain: {data}")
            return []
            
        chain_data = data.get("data", [])
        log(f"Fetched option chain with {len(chain_data)} strikes")
        return chain_data
        
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            print(f"401 Unauthorized: Invalid or expired live token. Response: {e.response.json()}. Regenerate at https://api-v2.upstox.com/developer/apps")
        else:
            print(f"HTTP error fetching option chain: {e}. Response: {e.response.json()}")
        return []
    except Exception as e:
        print(f"Error fetching option chain: {e}. Check network or API endpoint ({BASE_URL})")
        return []

def strike_prices(client: httpx.Client, price1: float, price2: float) -> Tuple[str, float, float]:
    log(f"Looking for strikes with LTP between ₹{price1}-{price2}")
    
    expiries = get_expiries(client)
    if not expiries:
        log("No expiry data — check your token scopes or instrument key.")
        return "0", 0, 0

    expiry = expiries[0]  # Use nearest expiry
    log(f"Using expiry: {expiry}")
    
    chain = get_option_chain(client, expiry)
    if not chain:
        log("No option chain data available.")
        return "0", 0, 0

    ce_match, pe_match = None, None
    valid_strikes = 0

    for opt in chain:
        strike = opt.get("strike_price")
        if not strike:
            continue
            
        valid_strikes += 1
        
        # Get call and put LTPs
        call_data = opt.get("call_options", {}).get("market_data", {})
        put_data = opt.get("put_options", {}).get("market_data", {})
        
        ce_ltp = call_data.get("ltp", 0)
        pe_ltp = put_data.get("ltp", 0)
        
        # Debug info for first few strikes
        if valid_strikes <= 5:
            log(f"Strike {strike}: CE_LTP={ce_ltp}, PE_LTP={pe_ltp}")

        # Look for CE match
        if ce_match is None and ce_ltp and price1 <= ce_ltp <= price2:
            ce_match = (strike, ce_ltp)
            log(f"✅ Found CE match: Strike {strike}, LTP ₹{ce_ltp}")

        # Look for PE match
        if pe_match is None and pe_ltp and price1 <= pe_ltp <= price2:
            pe_match = (strike, pe_ltp)
            log(f"✅ Found PE match: Strike {strike}, LTP ₹{pe_ltp}")

        # Break if we found both
        if ce_match and pe_match:
            break

    CE, PE = 0, 0
    if ce_match:
        CE = ce_match[0]
        log(f"Selected CE strike: {CE} @ ₹{ce_match[1]}")
    else:
        log(f"❌ No CE strike found in ₹{price1}–{price2} LTP range.")

    if pe_match:
        PE = pe_match[0]
        log(f"Selected PE strike: {PE} @ ₹{pe_match[1]}")
    else:
        log(f"❌ No PE strike found in ₹{price1}–{price2} LTP range.")

    log(f"Total valid strikes processed: {valid_strikes}")
    return expiry, CE, PE

def get_instrument_token(client: httpx.Client, expiry: str, strike: float, option_type: str) -> str:
    url = f"{BASE_URL}/option/chain"
    params = {
        'instrument_key': INSTRUMENT_KEY,
        'expiry_date': expiry
    }
    try:
        log(f"Getting instrument token for {option_type} {strike} expiry {expiry}")
        resp = client.get(url, headers=HEADERS, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get('status') == 'success':
            for chain_item in data.get('data', []):
                if chain_item.get('strike_price') == strike:
                    option_data = chain_item.get('call_options' if option_type == 'CE' else 'put_options')
                    if option_data and option_data.get('instrument_key'):
                        instrument_key = option_data['instrument_key']
                        token = instrument_key.split('|')[-1]
                        log(f"Found instrument token: {token} for {option_type} {strike}")
                        return token
                        
        log(f"No instrument token found for {option_type} {strike}")
        return None
        
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            print(f"401 Unauthorized: Invalid or expired live token. Response: {e.response.json()}. Regenerate at https://api-v2.upstox.com/developer/apps")
        else:
            print(f"HTTP error fetching instrument token: {e}. Response: {e.response.json()}")
        return None
    except Exception as e:
        print(f"Error fetching instrument token: {e}. Check network or API endpoint ({BASE_URL})")
        return None