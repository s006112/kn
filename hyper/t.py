import os
from dotenv import load_dotenv
from eth_account import Account

load_dotenv()

API_WALLET_KEY = os.getenv("HYPERLIQUID_API_WALLET_KEY")

print("SIGNER ADDRESS:", Account.from_key(API_WALLET_KEY).address)
