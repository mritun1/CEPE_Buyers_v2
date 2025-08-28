import requests
import json

def generate_access_token():
    """
    Generate Upstox access token using authorization code
    """
    # Step 1: Get these values from your Upstox app
    CLIENT_ID = "your_client_id"  # Replace with your actual client ID
    CLIENT_SECRET = "your_client_secret"  # Replace with your actual client secret
    AUTHORIZATION_CODE = "your_auth_code"  # Get this from the authorization URL
    REDIRECT_URI = "your_redirect_uri"  # Must match the one registered in your app
    
    # Step 2: Exchange authorization code for access token
    url = "https://api-v2.upstox.com/login/authorization/token"
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    payload = {
        "code": AUTHORIZATION_CODE,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code"
    }
    
    try:
        response = requests.post(url, headers=headers, data=payload)
        response.raise_for_status()
        
        token_data = response.json()
        print("Token generated successfully!")
        print(json.dumps(token_data, indent=2))
        
        # Save the access token
        access_token = token_data.get("access_token")
        if access_token:
            print(f"\nYour Access Token: {access_token}")
            print("\nAdd this to your key.py file:")
            print(f"SANDBOX_TOKEN = '{access_token}'")
            
        return token_data
        
    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error: {e}")
        print(f"Response: {e.response.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    generate_access_token()