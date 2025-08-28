import requests

# Try different possible base URLs and endpoints
POSSIBLE_URLS = [
    "https://api-sandbox.upstox.com/v2",
    "https://api.upstox.com/v2", 
    "https://api-sandbox.upstox.com",
    "https://api.upstox.com"
]

POSSIBLE_ENDPOINTS = [
    "/user/get-profile",
    "/user/profile", 
    "/profile",
    "/user",
    "/user/get_profile"
]

ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiI0QUNQVEYiLCJqdGkiOiI2OGE1M2EyYTQzODdjNjYzNDc5NjBlZmMiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc1NTY1ODc5NCwiaXNzIjoidWRhcGktZ2F0ZXdheS1zZXJ2aWNlIiwiZXhwIjoxNzU1NzI3MjAwfQ.74KOVGiDsb12eSHCnPHfaXuQzXUBSuzUWK7DXRerSkA"

headers = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json"
}

def test_endpoints():
    """Test different endpoint combinations"""
    print("Testing different endpoint combinations...")
    print("=" * 50)
    
    for base_url in POSSIBLE_URLS:
        print(f"\nTesting base URL: {base_url}")
        print("-" * 30)
        
        for endpoint in POSSIBLE_ENDPOINTS:
            full_url = f"{base_url}{endpoint}"
            print(f"Trying: {full_url}")
            
            try:
                resp = requests.get(full_url, headers=headers, timeout=10)
                print(f"  Status: {resp.status_code}")
                
                if resp.status_code == 200:
                    print(f"  ✅ SUCCESS! Response: {resp.json()}")
                    return full_url
                elif resp.status_code == 401:
                    print(f"  🔑 Unauthorized - Token issue")
                elif resp.status_code == 404:
                    print(f"  ❌ Not Found")
                else:
                    try:
                        print(f"  Response: {resp.json()}")
                    except:
                        print(f"  Response: {resp.text[:100]}")
                        
            except requests.exceptions.Timeout:
                print(f"  ⏱️ Timeout")
            except Exception as e:
                print(f"  ❌ Error: {e}")
    
    return None

def test_simple_connection():
    """Test just the basic connection without authentication"""
    print("\nTesting basic connection (no auth)...")
    print("=" * 50)
    
    base_urls = [
        "https://api-sandbox.upstox.com",
        "https://api.upstox.com"
    ]
    
    for url in base_urls:
        print(f"Testing: {url}")
        try:
            resp = requests.get(url, timeout=10)
            print(f"  Status: {resp.status_code}")
            print(f"  Headers: {dict(resp.headers)}")
        except Exception as e:
            print(f"  Error: {e}")

if __name__ == "__main__":
    # First test basic connectivity
    test_simple_connection()
    
    # Then test with authentication
    print(f"\nUsing ACCESS_TOKEN: {ACCESS_TOKEN[:20]}..." if len(ACCESS_TOKEN) > 20 else ACCESS_TOKEN)
    
    success_url = test_endpoints()
    
    if success_url:
        print(f"\n🎉 Found working endpoint: {success_url}")
        print("Use this URL structure in your main code!")
    else:
        print("\n❌ No working endpoint found.")
        print("Possible issues:")
        print("1. Invalid ACCESS_TOKEN")
        print("2. Sandbox environment might be down")
        print("3. Different API version or endpoint structure")
        print("4. Network/firewall blocking requests")