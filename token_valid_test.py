import requests
# from key import SANDBOX_TOKEN

SANDBOX_TOKEN = 'eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI0QUNQVEYiLCJqdGkiOiI2OGFmZDk2ZjA1Y2FmOTVhNTliN2EyMGEiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc1NjM1NDkyNywiaXNzIjoidWRhcGktZ2F0ZXdheS1zZXJ2aWNlIiwiZXhwIjoxNzU2NDE4NDAwfQ.e4v5gHtOxt7tceKWEO2gAHSdohMbH-0FEhLInGdpt2c'

def test_token():
    """Test if the Upstox token is valid"""
    
    if not SANDBOX_TOKEN:
        print("❌ No SANDBOX_TOKEN found in key.py")
        return False
    
    print(f"🔍 Testing token: {SANDBOX_TOKEN[:10]}...{SANDBOX_TOKEN[-10:]}")
    
    # Test with a simple API call
    url = "https://api-v2.upstox.com/user/profile"
    headers = {
        "Authorization": f"Bearer {SANDBOX_TOKEN}",
        "Accept": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            print("✅ Token is valid!")
            print(f"User: {data.get('data', {}).get('user_name', 'Unknown')}")
            return True
        elif response.status_code == 401:
            error_data = response.json()
            print("❌ Token is invalid or expired")
            print(f"Error: {error_data}")
            return False
        else:
            print(f"❌ Unexpected response: {response.status_code}")
            print(f"Response: {response.text}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Network error: {e}")
        return False

if __name__ == "__main__":
    if test_token():
        print("\n✅ Your token is working! You can run your trading script now.")
    else:
        print("\n❌ Token test failed. Please regenerate your token:")
        print("1. Go to https://api-v2.upstox.com/developer/apps")
        print("2. Create/select your app")
        print("3. Generate a new access token")
        print("4. Update your key.py file")