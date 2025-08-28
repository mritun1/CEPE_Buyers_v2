import requests
import json
from datetime import datetime
import os
from typing import Optional, Dict, Any

class UpstoxInstrumentFinder:
    def __init__(self, access_token: str):
        """
        Initialize with your Upstox access token
        
        Args:
            access_token: Your Upstox API access token
        """
        self.access_token = access_token
        self.base_url = "https://api.upstox.com/v2"
        self.headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json'
        }
        
        # Nifty 50 underlying instrument key
        self.nifty_underlying_key = "NSE_INDEX|Nifty 50"
    
    def get_option_contracts(self, expiry_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Get all option contracts for Nifty 50
        
        Args:
            expiry_date: Optional expiry date in YYYY-MM-DD format
            
        Returns:
            API response with option contracts
        """
        url = f"{self.base_url}/option/contracts/{self.nifty_underlying_key.replace('|', '%7C').replace(' ', '%20')}"
        
        params = {}
        if expiry_date:
            params['expiry_date'] = expiry_date
            
        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching option contracts: {e}")
            return {}
    
    def get_option_chain(self, expiry_date: str) -> Dict[str, Any]:
        """
        Get option chain for specific expiry
        
        Args:
            expiry_date: Expiry date in YYYY-MM-DD format
            
        Returns:
            API response with option chain
        """
        url = f"{self.base_url}/option/chain"
        
        params = {
            'instrument_key': self.nifty_underlying_key,
            'expiry_date': expiry_date
        }
        
        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching option chain: {e}")
            return {}
    
    def find_instrument_token(self, expiry: str, strike: float, option_type: str) -> Optional[Dict[str, Any]]:
        """
        Find instrument token for specific option parameters
        
        Args:
            expiry: Expiry date in YYYY-MM-DD format
            strike: Strike price as float
            option_type: "CE" for Call or "PE" for Put
            
        Returns:
            Dictionary with instrument details or None if not found
        """
        print(f"Searching for Nifty option:")
        print(f"Expiry: {expiry}")
        print(f"Strike: {strike}")
        print(f"Type: {option_type}")
        print("-" * 50)
        
        # Method 1: Try option contracts API
        print("Method 1: Using Option Contracts API...")
        contracts_data = self.get_option_contracts(expiry)
        
        if contracts_data.get('status') == 'success':
            for contract in contracts_data.get('data', []):
                if (contract.get('strike_price') == strike and 
                    contract.get('instrument_type') == option_type and
                    contract.get('expiry') == expiry):
                    
                    result = {
                        'instrument_key': contract.get('instrument_key'),
                        'exchange_token': contract.get('exchange_token'),
                        'trading_symbol': contract.get('trading_symbol'),
                        'strike_price': contract.get('strike_price'),
                        'expiry': contract.get('expiry'),
                        'instrument_type': contract.get('instrument_type'),
                        'lot_size': contract.get('lot_size'),
                        'tick_size': contract.get('tick_size'),
                        'weekly': contract.get('weekly'),
                        'method': 'option_contracts_api'
                    }
                    print("✅ Found via Option Contracts API!")
                    return result
        
        # Method 2: Try option chain API
        print("Method 2: Using Option Chain API...")
        chain_data = self.get_option_chain(expiry)
        
        if chain_data.get('status') == 'success':
            for chain_item in chain_data.get('data', []):
                if chain_item.get('strike_price') == strike:
                    option_data = chain_item.get('call_options' if option_type == 'CE' else 'put_options')
                    if option_data:
                        result = {
                            'instrument_key': option_data.get('instrument_key'),
                            'exchange_token': option_data.get('instrument_key', '').split('|')[-1] if option_data.get('instrument_key') else None,
                            'strike_price': strike,
                            'expiry': expiry,
                            'instrument_type': option_type,
                            'underlying_spot_price': chain_item.get('underlying_spot_price'),
                            'market_data': option_data.get('market_data', {}),
                            'option_greeks': option_data.get('option_greeks', {}),
                            'method': 'option_chain_api'
                        }
                        print("✅ Found via Option Chain API!")
                        return result
        
        print("❌ Instrument not found using API methods")
        return None
    
    def display_result(self, result: Optional[Dict[str, Any]]):
        """
        Display the found instrument details in a formatted way
        """
        if not result:
            print("\n🔍 SEARCH RESULT: NOT FOUND")
            print("The specified option contract was not found.")
            print("\nPossible reasons:")
            print("1. The expiry date might be incorrect or not available")
            print("2. The strike price might not be listed")
            print("3. The option might have expired or been delisted")
            print("4. Check if it's a weekly or monthly expiry")
            return
        
        print("\n🎯 INSTRUMENT FOUND!")
        print("=" * 60)
        print(f"📋 Instrument Key:    {result.get('instrument_key', 'N/A')}")
        print(f"🏷️  Exchange Token:    {result.get('exchange_token', 'N/A')}")
        print(f"📈 Trading Symbol:    {result.get('trading_symbol', 'N/A')}")
        print(f"💰 Strike Price:      ₹{result.get('strike_price', 'N/A')}")
        print(f"📅 Expiry:            {result.get('expiry', 'N/A')}")
        print(f"📊 Option Type:       {result.get('instrument_type', 'N/A')}")
        print(f"📦 Lot Size:          {result.get('lot_size', 'N/A')}")
        print(f"⏰ Weekly Option:     {result.get('weekly', 'N/A')}")
        print(f"🔧 Method Used:       {result.get('method', 'N/A')}")
        
        if result.get('underlying_spot_price'):
            print(f"💹 Nifty Spot Price:  ₹{result.get('underlying_spot_price', 'N/A')}")
        
        if result.get('market_data'):
            market_data = result['market_data']
            print(f"\n📊 MARKET DATA:")
            print(f"   Last Price:        ₹{market_data.get('ltp', 'N/A')}")
            print(f"   Volume:            {market_data.get('volume', 'N/A'):,}")
            print(f"   Open Interest:     {market_data.get('oi', 'N/A'):,}")
            
        print("=" * 60)


def main():
    """
    Main function to find instrument token
    """
    # Your search parameters
    expiry = "2025-08-21"
    strike = 24900.0
    option_type = "CE"  # "CE" for Call, "PE" for Put
    
    # IMPORTANT: Replace with your actual access token
    # You can get this from Upstox OAuth flow or from your app dashboard
    access_token = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI0QUNQVEYiLCJqdGkiOiI2OGE1M2EyYTQzODdjNjYzNDc5NjBlZmMiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc1NTY1ODc5NCwiaXNzIjoidWRhcGktZ2F0ZXdheS1zZXJ2aWNlIiwiZXhwIjoxNzU1NzI3MjAwfQ.74KOVGiDsb12eSHCnPHfaXuQzXUBSuzUWK7DXRerSkA"
    
    # Initialize the finder
    finder = UpstoxInstrumentFinder(access_token)
    
    # Find the instrument token
    result = finder.find_instrument_token(expiry, strike, option_type)
    
    # Display the result
    finder.display_result(result)
    
    # If found, show how to use it
    if result:
        print(f"\n🚀 USAGE EXAMPLE:")
        print(f"instrument_key = '{result.get('instrument_key')}'")
        print(f"# Use this instrument_key in other Upstox API calls")
        print(f"# Example: Get live quotes, place orders, etc.")


if __name__ == "__main__":
    main()