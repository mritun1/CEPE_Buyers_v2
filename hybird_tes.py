#!/usr/bin/env python3
"""
Hybrid Trading System Setup and Test Script
This script helps you set up and test your hybrid trading system
"""

import json
import requests
import os
import sys
from datetime import datetime as dt

def check_dependencies():
    """Check if required packages are installed"""
    required_packages = ['httpx', 'websocket-client', 'requests']
    missing_packages = []
    
    for package in required_packages:
        try:
            __import__(package.replace('-', '_'))
        except ImportError:
            missing_packages.append(package)
    
    if missing_packages:
        print("❌ Missing required packages:")
        for package in missing_packages:
            print(f"   - {package}")
        print("\n📦 Install missing packages with:")
        print(f"   pip install {' '.join(missing_packages)}")
        return False
    
    print("✅ All required packages are installed")
    return True

def check_key_file():
    """Check if key.py exists and has tokens"""
    try:
        from key import LIVE_TOKEN, SANDBOX_TOKEN
        
        if not LIVE_TOKEN or LIVE_TOKEN == "your_live_access_token_here":
            print("❌ LIVE_TOKEN not set in key.py")
            print("   Please set your live token for market data access")
            return False
        
        if len(LIVE_TOKEN) < 100:  # JWT tokens are typically much longer
            print("⚠️  LIVE_TOKEN seems too short - are you sure it's correct?")
        
        print("✅ key.py file configured")
        print(f"   LIVE_TOKEN: {LIVE_TOKEN[:10]}...{LIVE_TOKEN[-5:]}")
        return True
        
    except ImportError:
        print("❌ key.py file not found")
        print("   Create key.py with your tokens")
        return False
    except Exception as e:
        print(f"❌ Error reading key.py: {e}")
        return False

def test_api_connection():
    """Test connection to Upstox API"""
    try:
        from key import LIVE_TOKEN
        
        print("🔍 Testing API connection...")
        
        headers = {
            "Authorization": f"Bearer {LIVE_TOKEN}",
            "Accept": "application/json"
        }
        
        # Test with user profile endpoint
        url = "https://api-v2.upstox.com/user/profile"
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            user_name = data.get('data', {}).get('user_name', 'Unknown')
            print(f"✅ API connection successful")
            print(f"   User: {user_name}")
            return True
        elif response.status_code == 401:
            print("❌ API authentication failed")
            print("   Your token may be expired or invalid")
            print("   Generate a new token at: https://api-v2.upstox.com/developer/apps")
            return False
        else:
            print(f"❌ API error: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error testing API: {e}")
        return False

def test_market_data():
    """Test market data access"""
    try:
        from key import LIVE_TOKEN
        
        print("📊 Testing market data access...")
        
        headers = {
            "Authorization": f"Bearer {LIVE_TOKEN}",
            "Accept": "application/json"
        }
        
        # Test Nifty LTP
        url = "https://api-v2.upstox.com/market-quote/ltp"
        params = {"instrument_key": "NSE_INDEX|Nifty 50"}
        
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                ltp_data = data.get("data", {})
                if ltp_data:
                    instrument, info = next(iter(ltp_data.items()))
                    ltp = info.get("last_price")
                    print(f"✅ Market data access successful")
                    print(f"   Nifty 50 LTP: ₹{ltp}")
                    return True
                else:
                    print("❌ No market data in response")
                    return False
            else:
                print(f"❌ Market data API error: {data}")
                return False
        else:
            print(f"❌ Market data request failed: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error testing market data: {e}")
        return False

def test_option_chain():
    """Test option chain access"""
    try:
        import httpx
        from key import LIVE_TOKEN
        
        print("🔗 Testing option chain access...")
        
        headers = {
            "Authorization": f"Bearer {LIVE_TOKEN}",
            "Accept": "application/json"
        }
        
        # Get expiries first
        with httpx.Client() as client:
            url = "https://api-v2.upstox.com/option/contract"
            params = {"instrument_key": "NSE_INDEX|Nifty 50"}
            
            response = client.get(url, headers=headers, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    expiries = [item.get("expiry") for item in data.get("data", []) if item.get("expiry")]
                    if expiries:
                        print(f"✅ Option chain access successful")
                        print(f"   Available expiries: {len(expiries)}")
                        print(f"   Nearest expiry: {sorted(expiries)[0]}")
                        return True
                    else:
                        print("❌ No expiries found in option contract data")
                        return False
                else:
                    print(f"❌ Option contract API error: {data}")
                    return False
            else:
                print(f"❌ Option contract request failed: {response.status_code}")
                print(f"   Response: {response.text}")
                return False
                
    except Exception as e:
        print(f"❌ Error testing option chain: {e}")
        return False

def create_config_summary():
    """Create a summary of the current configuration"""
    try:
        from key import LIVE_TOKEN, SANDBOX_TOKEN
        
        config = {
            "timestamp": dt.now().isoformat(),
            "environment": "hybrid",
            "data_source": "live",
            "trading_mode": "paper",
            "live_token_set": bool(LIVE_TOKEN and LIVE_TOKEN != "your_live_access_token_here"),
            "sandbox_token_set": bool(SANDBOX_TOKEN and SANDBOX_TOKEN != "your_sandbox_access_token_here")
        }
        
        with open("config_summary.json", "w") as f:
            json.dump(config, f, indent=2)
        
        print("📝 Configuration summary saved to config_summary.json")
        return True
        
    except Exception as e:
        print(f"❌ Error creating config summary: {e}")
        return False

def run_system_check():
    """Run complete system check"""
    print("🚀 HYBRID TRADING SYSTEM - SETUP CHECK")
    print("=" * 50)
    
    checks = [
        ("Dependencies", check_dependencies),
        ("Key File", check_key_file),
        ("API Connection", test_api_connection),
        ("Market Data", test_market_data),
        ("Option Chain", test_option_chain),
        ("Config Summary", create_config_summary)
    ]
    
    passed = 0
    total = len(checks)
    
    for check_name, check_func in checks:
        print(f"\n🔍 {check_name}...")
        if check_func():
            passed += 1
        else:
            print(f"❌ {check_name} check failed")
    
    print("\n" + "=" * 50)
    print(f"📊 SYSTEM CHECK RESULTS: {passed}/{total} checks passed")
    
    if passed == total:
        print("🎉 All checks passed! Your system is ready for hybrid trading.")
        print("\n🚀 To start trading, run:")
        print("   python3 hybrid_trading.py")
    else:
        print("⚠️  Some checks failed. Please fix the issues before starting trading.")
    
    print("\n📋 SYSTEM CONFIGURATION:")
    print("   📊 Data Source: LIVE (real market data)")
    print("   💼 Trading Mode: PAPER (simulated trades)")
    print("   🔒 Risk Level: ZERO (no real money at risk)")
    
    return passed == total

def show_help():
    """Show help information"""
    help_text = """
🚀 HYBRID TRADING SYSTEM HELP

OVERVIEW:
This system uses LIVE market data for accurate price feeds while executing 
PAPER trades to eliminate financial risk. Perfect for testing strategies!

SETUP STEPS:
1. Install dependencies: pip install httpx websocket-client requests
2. Create key.py with your live Upstox token
3. Run this setup script: python3 setup_and_test.py
4. If all checks pass, start trading: python3 hybrid_trading.py

KEY FILES:
- key.py: Your API tokens
- hybrid_trading.py: Main trading script
- findstrikeprice.py: Options selection logic
- setup_and_test.py: This setup script

TOKENS:
- LIVE_TOKEN: Required for real market data (expires daily)
- Get from: https://api-v2.upstox.com/developer/apps

FEATURES:
✅ Real market data (no delays)
✅ Paper trading (zero risk)  
✅ Full trade logging
✅ P&L calculation with charges
✅ Automatic instrument selection
✅ Trail stop and stop loss
✅ WebSocket real-time feeds

SUPPORT:
- Check logs for detailed error messages
- Ensure your app has market data permissions
- Regenerate tokens if authentication fails
    """
    print(help_text)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ["--help", "-h", "help"]:
        show_help()
    else:
        success = run_system_check()
        
        if not success:
            print(f"\n🆘 Need help? Run: python3 {sys.argv[0]} --help")
        else:
            print(f"\n🎯 Next step: python3 hybrid_trading.py")