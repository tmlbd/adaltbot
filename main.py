import os, asyncio, datetime, uvicorn, random, re, subprocess
import aiohttp
from fastapi import FastAPI, Body, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from motor.motor_asyncio import AsyncIOMotorClient
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bson import ObjectId
from pydantic import BaseModel
from PIL import Image # নতুন ইমেজ লাইব্রেরি
from pyrogram import Client as PyroClient # ২ জিবি ফাইল সাপোর্ট করার জন্য এড করা হলো

# --- কনফিগারেশন ---
TOKEN = os.getenv("BOT_TOKEN", "8726013622:AAHe9hpP52qVyNtleia2QtnCu7UJc0mJXOI")
MONGO_URL = os.getenv("MONGO_URL", "mongodb+srv://akash:akash@cluster0.etisrpx.mongodb.net/?appName=Cluster0")
OWNER_ID = int(os.getenv("ADMIN_ID", "7525127704")) 
APP_URL = os.getenv("APP_URL", "https://rare-rori-yeasinvai-bf8e2c68.koyeb.app/")
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003536485803") # আপনার চ্যানেলের আইডি এখানে দিন

# ২ জিবি ফাইল ডাউনলোডের জন্য API ID এবং HASH (এড করা হলো)
API_ID = int(os.getenv("API_ID", "29904834"))
API_HASH = os.getenv("API_HASH", "8b4fd9ef578af114502feeafa2d31938")

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()

# Pyrogram ক্লায়েন্ট সেটআপ (বড় ফাইল হ্যান্ডেল করার জন্য)
pyro_bot = PyroClient("bot_pyro_session", api_id=API_ID, api_hash=API_HASH, bot_token=TOKEN)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

client = AsyncIOMotorClient(MONGO_URL)
db = client['moviedatabase']

admin_temp = {}
admin_cache = set([OWNER_ID]) 

# --- ভিডিও ডিউরেশন ফরম্যাট করার ফাংশন ---
def get_duration_display(seconds):
    try:
        seconds = int(float(seconds))
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        if h > 0: return f"{h}h {m}m"
        return f"{m}m {s}s"
    except: return "0m"

# --- নতুন স্ক্রিনশট গ্রিড ফাংশন (৬টি স্ক্রিনশট ২ সারি ৩ কলাম ল্যান্ডস্কেপ গ্রিড) ---
async def create_screenshot_grid(video_path, output_path):
    try:
        # ভিডিওর ডিউরেশন বের করা
        cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{video_path}"'
        duration = float(subprocess.check_output(cmd, shell=True).decode().strip())
        
        interval = duration / 7 # ৬টি গ্রিডের জন্য গ্যাপ
        screenshots = []
        for i in range(1, 7):
            time_pos = i * interval
            temp_out = f"temp_frame_{i}_{random.randint(100,999)}.jpg"
            # FFMPEG দিয়ে ফ্রেম নেওয়া
            subprocess.run(f'ffmpeg -ss {time_pos} -i "{video_path}" -vframes 1 -q:v 2 "{temp_out}" -y', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(temp_out):
                screenshots.append(temp_out)
        
        if not screenshots: return False, 0
        
        # PIL দিয়ে গ্রিড তৈরি (৩ কলাম, ২ সারি)
        images = [Image.open(x) for x in screenshots]
        w, h = images[0].size
        grid = Image.new('RGB', (w*3, h*2))
        
        for idx, img in enumerate(images):
            x = (idx % 3) * w
            y = (idx // 3) * h
            grid.paste(img, (x, y))
            img.close()
            os.remove(screenshots[idx])
            
        grid.save(output_path)
        return True, duration
    except Exception:
        return False, 0

# --- নতুন টাইম পার্সার ---
def parse_duration(time_str):
    total_seconds = 0
    hours = re.search(r'(\d+)h', time_str)
    minutes = re.search(r'(\d+)m', time_str)
    seconds = re.search(r'(\d+)s', time_str)
    if hours: total_seconds += int(hours.group(1)) * 3600
    if minutes: total_seconds += int(minutes.group(1)) * 60
    if seconds: total_seconds += int(seconds.group(1))
    if not any([hours, minutes, seconds]) and time_str.isdigit():
        total_seconds = int(time_str) * 3600 # যদি শুধু সংখ্যা দেয় তবে ঘন্টা হিসেবে ধরবে
    return total_seconds if total_seconds > 0 else 86400

# --- নতুন ব্লিংক লিঙ্ক পার্সার ---
def parse_blink_link(link):
    pattern = r"t\.me/(?:c/)?([^/]+)/(\d+)"
    match = re.search(pattern, link)
    if match:
        chat_id = match.group(1)
        if chat_id.isdigit(): chat_id = int("-100" + chat_id)
        msg_id = int(match.group(2))
        return chat_id, msg_id
    return None, None

async def load_admins():
    admin_cache.clear()
    admin_cache.add(OWNER_ID)
    try:
        async for admin in db.admins.find():
            admin_cache.add(admin["user_id"])
    except Exception: pass

# --- ব্যাকগ্রাউন্ড অটো-ডিলিট ওয়ার্কার ---
async def auto_delete_worker():
    while True:
        try:
            now = datetime.datetime.utcnow()
            expired_msgs = db.auto_delete.find({"delete_at": {"$lte": now}})
            async for msg in expired_msgs:
                try:
                    await bot.delete_message(chat_id=msg["chat_id"], message_id=msg["message_id"])
                except Exception: pass
                await db.auto_delete.delete_one({"_id": msg["_id"]})
        except Exception: pass
        await asyncio.sleep(60)

# --- নতুন ব্লিংক ইন্ডেক্সিং ওয়ার্কার (১ থেকে লাস্ট আইডি পর্যন্ত) ---
async def blink_worker(chat_id, last_id, admin_id):
    await bot.send_message(admin_id, f"🚀 <b>ব্লিংক প্রসেস শুরু হয়েছে!</b>\nআইডি 1 থেকে {last_id} পর্যন্ত ফাইল চেক করে অটো আপলোড করা হচ্ছে।", parse_mode="HTML")
    
    success_count = 0
    for current_id in range(1, last_id + 1):
        try:
            msg = await pyro_bot.get_messages(chat_id, current_id)
            if not msg or msg.empty: continue
            
            if msg.video or (msg.document and "video" in msg.document.mime_type):
                serial_res = await db.settings.find_one_and_update(
                    {"id": "auto_post_count"},
                    {"$inc": {"value": 1}},
                    upsert=True,
                    return_document=True
                )
                count_val = serial_res.get('value', 1)
                title = f"🥵 New Hot Sex Video Number🥵 হট সেক্স ভাইরাল ভিডিও নাম্বার 🥵 {count_val}"

                video_path = await pyro_bot.download_media(msg)
                grid_path = f"grid_blink_{random.randint(1000,9999)}.jpg"
                
                res, duration_sec = await create_screenshot_grid(video_path, grid_path)
                duration_str = get_duration_display(duration_sec) if res else "0m"

                if res:
                    with open(grid_path, 'rb') as f:
                        photo_msg = await bot.send_photo(CHANNEL_ID, photo=types.BufferedInputFile(f.read(), filename="grid.jpg"), 
                        caption=f"🎥 <b>নতুন মুভি যুক্ত হয়েছে!</b>\n\n🎬 নাম: <b>{title}</b>", parse_mode="HTML",
                        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="🎬 মুভিটি দেখুন", url=f"https://t.me/{(await bot.get_me()).username}?start=new")]]))
                        photo_id = photo_msg.photo[-1].file_id

                    await db.movies.insert_one({
                        "title": title, "photo_id": photo_id, "file_id": (msg.video.file_id if msg.video else msg.document.file_id), 
                        "file_type": "video", "clicks": 0, "duration": duration_str, "created_at": datetime.datetime.utcnow()
                    })
                    success_count += 1
                
                if os.path.exists(video_path): os.remove(video_path)
                if os.path.exists(grid_path): os.remove(grid_path)
                await asyncio.sleep(1.5)
        except Exception: continue

    await bot.send_message(admin_id, f"✅ <b>ব্লিংক ইন্ডেক্স সম্পন্ন!</b>\nমোট <b>{success_count}</b>টি ভিডিও সফলভাবে আপলোড হয়েছে।", parse_mode="HTML")

# ==========================================
# ১. মেইন ওনার (Owner) ও অ্যাডমিন কমান্ড সমূহ
# ==========================================

@dp.message(Command("addadmin"))
async def add_admin_cmd(m: types.Message):
    if m.from_user.id != OWNER_ID: return
    try:
        new_admin = int(m.text.split()[1])
        if new_admin in admin_cache:
            return await m.answer("⚠️ এই ইউজারটি আগে থেকেই অ্যাডমিন!")
        await db.admins.insert_one({"user_id": new_admin})
        admin_cache.add(new_admin)
        await m.answer(f"✅ নতুন অ্যাডমিন যুক্ত করা হয়েছে: <code>{new_admin}</code>", parse_mode="HTML")
        try: await bot.send_message(new_admin, "🎉 <b>অভিনন্দন!</b> আপনাকে এই বটের অ্যাডমিন বানানো হয়েছে।", parse_mode="HTML")
        except: pass
    except: await m.answer("⚠️ সঠিক নিয়ম: <code>/addadmin ইউজার_আইডি</code>", parse_mode="HTML")

@dp.message(Command("deladmin"))
async def del_admin_cmd(m: types.Message):
    if m.from_user.id != OWNER_ID: return
    try:
        del_admin = int(m.text.split()[1])
        if del_admin == OWNER_ID: return await m.answer("⚠️ আপনি নিজেকে (Owner) ডিলিট করতে পারবেন না!")
        await db.admins.delete_one({"user_id": del_admin})
        admin_cache.discard(del_admin)
        await m.answer(f"✅ অ্যাডমিন রিমুভ করা হয়েছে: <code>{del_admin}</code>", parse_mode="HTML")
    except: await m.answer("⚠️ সঠিক নিয়ম: <code>/deladmin ইউজার_আইডি</code>", parse_mode="HTML")

@dp.message(Command("adminlist"))
async def list_admins_cmd(m: types.Message):
    if m.from_user.id != OWNER_ID: return
    text = "👥 <b>বর্তমান অ্যাডমিন লিস্ট:</b>\n"
    text += f"👑 Owner: <code>{OWNER_ID}</code>\n"
    for ad in admin_cache:
        if ad != OWNER_ID: text += f"👮 Admin: <code>{ad}</code>\n"
    await m.answer(text, parse_mode="HTML")

@dp.message(Command("addlink"))
async def add_ad_link(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        url = m.text.split(" ", 1)[1]
        await db.ad_links.insert_one({"url": url, "created_at": datetime.datetime.utcnow()})
        await m.answer(f"✅ নতুন অ্যাড লিঙ্ক যুক্ত হয়েছে:\n<code>{url}</code>", parse_mode="HTML")
    except: await m.answer("⚠️ নিয়ম: `/addlink https://directlink.com`")

@dp.message(Command("links"))
async def list_ad_links(m: types.Message):
    if m.from_user.id not in admin_cache: return
    links = await db.ad_links.find().to_list(length=100)
    if not links: return await m.answer("কোনো অ্যাড লিঙ্ক নেই।")
    text = "🔗 <b>বর্তমান অ্যাড লিঙ্ক সমূহ:</b>\n\n"
    for i, l in enumerate(links):
        text += f"{i+1}. <code>{l['url']}</code>\nID: <code>{l['_id']}</code>\n\n"
    text += "ডিলিট করতে: `/dellink ID` লিখুন।"
    await m.answer(text, parse_mode="HTML")

@dp.message(Command("dellink"))
async def del_ad_link(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        link_id = m.text.split(" ", 1)[1]
        await db.ad_links.delete_one({"_id": ObjectId(link_id)})
        await m.answer("✅ লিঙ্কটি ডিলিট করা হয়েছে।")
    except: await m.answer("⚠️ নিয়ম: `/dellink ID` (ID পাবেন /links কমান্ডে)")

@dp.message(Command("monetag"))
async def toggle_monetag(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        status = m.text.split(" ")[1].lower() == "on"
        await db.settings.update_one({"id": "monetag_status"}, {"$set": {"status": status}}, upsert=True)
        await m.answer(f"✅ Monetag Ads এখন {'চালু (ON)' if status else 'বন্ধ (OFF)'}")
    except: await m.answer("⚠️ নিয়ম: `/monetag on` বা `/monetag off`")

@dp.message(Command("adlink"))
async def toggle_adlink(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        status = m.text.split(" ")[1].lower() == "on"
        await db.settings.update_one({"id": "adlink_status"}, {"$set": {"status": status}}, upsert=True)
        await m.answer(f"✅ Direct Link Ads এখন {'চালু (ON)' if status else 'বন্ধ (OFF)'}")
    except: await m.answer("⚠️ নিয়ম: `/adlink on` বা `/adlink off`")

@dp.message(Command("setsteps"))
async def set_steps_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        steps = int(m.text.split(" ")[1])
        await db.settings.update_one({"id": "step_config"}, {"$set": {"count": steps}}, upsert=True)
        await m.answer(f"✅ অ্যাড স্টেপ সংখ্যা এখন: <b>{steps}</b>", parse_mode="HTML")
    except: await m.answer("⚠️ নিয়ম: `/setsteps 2`")

@dp.message(Command("setunlocktime"))
async def set_unlock_time_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        raw_val = m.text.split(" ", 1)[1]
        seconds = parse_duration(raw_val)
        await db.settings.update_one({"id": "unlock_config"}, {"$set": {"seconds": seconds, "raw": raw_val}}, upsert=True)
        await m.answer(f"✅ মুভি আনলক সময় সেট করা হয়েছে: <b>{raw_val}</b>", parse_mode="HTML")
    except: await m.answer("⚠️ নিয়ম: <code>/setunlocktime 1h,1m,10s</code>")

@dp.message(Command("setnotice"))
async def set_notice_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        notice = m.text.split(" ", 1)[1]
        await db.settings.update_one({"id": "site_notice"}, {"$set": {"text": notice}}, upsert=True)
        await m.answer("✅ নোটিশ আপডেট করা হয়েছে।")
    except: await m.answer("⚠️ নিয়ম: `/setnotice মুভি না চললে ভিপিএন অন করুন`")

@dp.message(Command("sethead"))
async def set_head_ad(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        code = m.text.split(" ", 1)[1]
        await db.settings.update_one({"id": "header_ad"}, {"$set": {"code": code}}, upsert=True)
        await m.answer("✅ হেডার ব্যানার অ্যাড কোড আপডেট হয়েছে।")
    except: await m.answer("⚠️ নিয়ম: `/sethead [অ্যাড কোড]`")

@dp.message(Command("setmid"))
async def set_mid_ad(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        code = m.text.split(" ", 1)[1]
        await db.settings.update_one({"id": "middle_ad"}, {"$set": {"code": code}}, upsert=True)
        await m.answer("✅ মিডেল ব্যানার অ্যাড কোড আপডেট হয়েছে।")
    except: await m.answer("⚠️ নিয়ম: `/setmid [অ্যাড কোড]`")

@dp.message(Command("setfoot"))
async def set_foot_ad(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        code = m.text.split(" ", 1)[1]
        await db.settings.update_one({"id": "footer_ad"}, {"$set": {"code": code}}, upsert=True)
        await m.answer("✅ ফুটার ব্যানার অ্যাড কোড আপডেট হয়েছে।")
    except: await m.answer("⚠️ নিয়ম: `/setfoot [অ্যাড কোড]`")

@dp.message(Command("blink"))
async def blink_cmd_handler(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        link = m.text.split(" ", 1)[1]
        chat_id, last_id = parse_blink_link(link)
        if not chat_id or not last_id:
            return await m.answer("⚠️ <b>ভুল লিঙ্ক!</b> সঠিক উদাহরণ: <code>/blink https://t.me/your_channel/1234</code>", parse_mode="HTML")
        asyncio.create_task(blink_worker(chat_id, last_id, m.from_user.id))
        await m.answer("⏳ চ্যানেলের আইডি 1 থেকে শুরু করে সব ভিডিও ইন্ডেক্স করা হচ্ছে।")
    except: await m.answer("⚠️ নিয়ম: <code>/blink [চ্যানেলের শেষ পোস্ট লিঙ্ক]</code>", parse_mode="HTML")

# ==========================================
# ২. বটের সাধারণ অ্যাডমিন কমান্ড
# ==========================================

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await db.users.update_one({"user_id": message.from_user.id}, {"$set": {"first_name": message.from_user.first_name}}, upsert=True)
    kb = [[types.InlineKeyboardButton(text="🎬 ওপেন মুভি অ্যাপ", web_app=types.WebAppInfo(url=APP_URL))]]
    markup = types.InlineKeyboardMarkup(inline_keyboard=kb)
    uid = message.from_user.id
    if uid in admin_cache:
        text = "👋 <b>হ্যালো অ্যাডমিন!</b>\n\n⚙️ <b>পোস্ট কমান্ড:</b>\n🔹 <code>/post</code> - ম্যানুয়াল\n🔹 <code>/new</code> - অটো স্ক্রিনশট\n🔹 <code>/auto</code> - ফুল অটো\n🔹 <code>/blink</code> - ইন্ডেক্স\n"
    else: text = f"👋 <b>স্বাগতম {message.from_user.first_name}!</b>\n\n[আপনার টেলিগ্রাম আইডি: <code>{uid}</code>]\n\nমুভি দেখতে নিচের বাটনে ক্লিক করুন।"
    await message.answer(text, reply_markup=markup, parse_mode="HTML")

@dp.message(Command("post"))
async def post_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    admin_temp[m.from_user.id] = {"step": "manual_file"}
    await m.answer("📥 <b>ম্যানুয়াল আপলোড:</b> মুভি ফাইলটি (Video/Doc) পাঠান।")

@dp.message(Command("new"))
async def new_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    admin_temp[m.from_user.id] = {"step": "auto_name"}
    await m.answer("🆕 <b>অটো আপলোড:</b> মুভির নাম লিখে পাঠান।")

@dp.message(Command("auto"))
async def auto_post_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    admin_temp[m.from_user.id] = {"step": "auto_serial_mode"}
    await m.answer("🤖 <b>ফুল অটো মোড:</b> শুধু ভিডিও ফাইলটি পাঠান।")

@dp.message(Command("setsitename"))
async def set_site_name(m: types.Message):
    if m.from_user.id in admin_cache:
        try:
            new_name = m.text.split(" ", 1)[1]
            await db.settings.update_one({"id": "site_name"}, {"$set": {"name": new_name}}, upsert=True)
            await m.answer(f"✅ সাইটের নাম পরিবর্তন করে <b>{new_name}</b> রাখা হয়েছে।", parse_mode="HTML")
        except: await m.answer("⚠️ সঠিক নিয়ম: <code>/setsitename My Movie Site</code>", parse_mode="HTML")

@dp.message(Command("protect"))
async def protect_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    try:
        state = m.text.split(" ")[1].lower()
        if state == "on":
            await db.settings.update_one({"id": "protect_content"}, {"$set": {"status": True}}, upsert=True)
            await m.answer("✅ ফরোয়ার্ড প্রোটেকশন <b>চালু (ON)</b> করা হয়েছে।", parse_mode="HTML")
        elif state == "off":
            await db.settings.update_one({"id": "protect_content"}, {"$set": {"status": False}}, upsert=True)
            await m.answer("✅ ফরোয়ার্ড প্রোটেকশন <b>বন্ধ (OFF)</b> করা হয়েছে।", parse_mode="HTML")
    except: pass

@dp.message(Command("stats"))
async def stats_cmd(m: types.Message):
    if m.from_user.id not in admin_cache: return
    uc = await db.users.count_documents({})
    mc = await db.movies.count_documents({})
    await m.answer(f"📊 <b>স্ট্যাটাস:</b>\n👥 মোট ইউজার: <code>{uc}</code>\n🎬 মোট মুভি: <code>{mc}</code>", parse_mode="HTML")

@dp.message(Command("del"))
async def del_movie_list(m: types.Message):
    if m.from_user.id not in admin_cache: return
    movies = await db.movies.find().sort("created_at", -1).limit(20).to_list(length=20)
    if not movies: return await m.answer("কোনো মুভি নেই।")
    builder = InlineKeyboardBuilder()
    for mv in movies: builder.button(text=f"❌ {mv['title']}", callback_data=f"del_{str(mv['_id'])}")
    builder.adjust(1)
    await m.answer("⚠️ ডিলিট করতে ক্লিক করুন:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("del_"))
async def del_movie_callback(c: types.CallbackQuery):
    if c.from_user.id not in admin_cache: return
    try:
        await db.movies.delete_one({"_id": ObjectId(c.data.split("_")[1])})
        await c.answer("✅ ডিলিট হয়েছে!", show_alert=True)
        await c.message.edit_text("✅ মুভিটি ডাটাবেস থেকে মুছে ফেলা হয়েছে।", reply_markup=None)
    except: pass

@dp.message(Command("settime"))
async def set_del_time(m: types.Message):
    if m.from_user.id in admin_cache:
        try:
            await db.settings.update_one({"id": "del_time"}, {"$set": {"minutes": int(m.text.split(" ")[1])}}, upsert=True)
            await m.answer(f"✅ অটো-ডিলিট টাইম সেট করা হয়েছে।")
        except: await m.answer("⚠️ সঠিক নিয়ম: <code>/settime 60</code>", parse_mode="HTML")

@dp.message(Command("settg"))
async def set_tg(m: types.Message):
    if m.from_user.id in admin_cache:
        try:
            await db.settings.update_one({"id": "link_tg"}, {"$set": {"url": m.text.split(" ")[1]}}, upsert=True)
            await m.answer("✅ টেলিগ্রাম লিংক আপডেট হয়েছে।")
        except: pass

@dp.message(Command("set18"))
async def set_18(m: types.Message):
    if m.from_user.id in admin_cache:
        try:
            await db.settings.update_one({"id": "link_18"}, {"$set": {"url": m.text.split(" ")[1]}}, upsert=True)
            await m.answer("✅ 18+ লিংক আপডেট হয়েছে।")
        except: pass

# --- ইনপুট প্রসেসিং ---
@dp.message(Command("cast"))
async def broadcast_prep(m: types.Message):
    if m.from_user.id not in admin_cache: return
    admin_temp[m.from_user.id] = {"step": "bcast_wait"}
    await m.answer("📢 <b>অ্যাডভান্সড ব্রডকাস্ট:</b> মেসেজটি পাঠান।", parse_mode="HTML")

@dp.callback_query(F.data.startswith("reply_"))
async def process_reply_cb(c: types.CallbackQuery):
    if c.from_user.id not in admin_cache: return
    user_id = int(c.data.split("_")[1])
    admin_temp[c.from_user.id] = {"step": "reply_user", "target_uid": user_id}
    await c.message.reply("✍️ <b>ইউজারকে কী রিপ্লাই দিতে চান তা লিখে পাঠান:</b>", parse_mode="HTML")
    await c.answer()

@dp.message(F.content_type.in_({'text', 'photo', 'video', 'document', 'voice'}))
async def catch_all_inputs(m: types.Message):
    uid = m.from_user.id
    state = admin_temp.get(uid, {}).get("step")
    
    if uid in admin_cache and state == "reply_user":
        target_uid = admin_temp[uid]["target_uid"]
        del admin_temp[uid]
        try:
            if m.text: await bot.send_message(target_uid, f"📩 <b>অ্যাডমিন রিপ্লাই:</b>\n\n{m.text}", parse_mode="HTML")
            else: await m.copy_to(target_uid, caption=f"📩 <b>অ্যাডমিন রিপ্লাই:</b>\n\n{m.caption or ''}", parse_mode="HTML")
            await m.answer("✅ ইউজারকে সফলভাবে রিপ্লাই পাঠানো হয়েছে!")
        except Exception: await m.answer("⚠️ এরর!")
        return

    if uid in admin_cache and state == "bcast_wait":
        del admin_temp[uid]
        await m.answer("⏳ শুরু হয়েছে...")
        kb = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="🎬 ওপেন মুভি অ্যাপ", web_app=types.WebAppInfo(url=APP_URL))]])
        success = 0
        async for u in db.users.find():
            try:
                await m.copy_to(chat_id=u['user_id'], reply_markup=kb)
                success += 1
                await asyncio.sleep(0.05)
            except: pass
        await m.answer(f"✅ সম্পন্ন! মোট {success}")
        return

    if uid in admin_cache and (m.document or m.video) and state == "manual_file":
        fid = m.video.file_id if m.video else m.document.file_id
        ftype = "video" if m.video else "document"
        admin_temp[uid] = {"step": "manual_photo", "file_id": fid, "type": ftype}
        await m.answer("✅ ফাইল পেয়েছি! এবার মুভির <b>পোস্টার (Photo)</b> সেন্ড করুন。", parse_mode="HTML")
        return

    if uid in admin_cache and m.photo and state == "manual_photo":
        admin_temp[uid]["photo_id"] = m.photo[-1].file_id
        admin_temp[uid]["step"] = "manual_title"
        await m.answer("✅ পোস্টার পেয়েছি! এবার মুভির <b>নাম</b> লিখে পাঠান。", parse_mode="HTML")
        return

    if uid in admin_cache and m.text and state == "manual_title":
        title = m.text.strip()
        await db.movies.insert_one({
            "title": title, "photo_id": admin_temp[uid]["photo_id"], 
            "file_id": admin_temp[uid]["file_id"], "file_type": admin_temp[uid]["type"], 
            "clicks": 0, "duration": "Unknown", "created_at": datetime.datetime.utcnow()
        })
        me = await bot.get_me()
        kb = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="🎬 মুভিটি দেখুন", url=f"https://t.me/{me.username}?start=new")]])
        await bot.send_photo(CHANNEL_ID, photo=admin_temp[uid]["photo_id"], caption=f"🎥 <b>নতুন মুভি!</b>\n\n🎬 নাম: <b>{title}</b>", parse_mode="HTML", reply_markup=kb)
        del admin_temp[uid]
        await m.answer(f"🎉 <b>{title}</b> ম্যানুয়ালি যুক্ত করা হয়েছে!")
        return

    if uid in admin_cache and m.text and state == "auto_name":
        admin_temp[uid] = {"step": "auto_file", "title": m.text.strip()}
        await m.answer(f"✅ মুভি: <b>{m.text}</b>\nএবার ভিডিও ফাইলটি পাঠান (Max 2GB)。", parse_mode="HTML")
        return

    if uid in admin_cache and (m.document or m.video) and (state == "auto_file" or state == "auto_serial_mode"):
        file = m.video or m.document
        status_msg = await m.answer("⏳ প্রসেসিং শুরু হয়েছে...")
        try:
            if state == "auto_serial_mode":
                serial_res = await db.settings.find_one_and_update({"id": "auto_post_count"},{"$inc": {"value": 1}},upsert=True,return_document=True)
                title = f"🥵 New Hot Sex Video Number🥵 {serial_res.get('value', 1)}"
            else: title = admin_temp[uid]["title"]

            video_path = await pyro_bot.download_media(m.video or m.document)
            grid_path = f"grid_poster_{uid}_{random.randint(100,999)}.jpg"
            res, duration_sec = await create_screenshot_grid(video_path, grid_path)
            duration_str = get_duration_display(duration_sec) if res else "0m"
            
            if res:
                with open(grid_path, 'rb') as f:
                    photo_msg = await bot.send_photo(m.chat.id, photo=types.BufferedInputFile(f.read(), filename="grid.jpg"), caption=f"🎬 {title}\n⏳ Duration: {duration_str}")
                    photo_id = photo_msg.photo[-1].file_id
                
                ftype = "video" if m.video else "document"
                await db.movies.insert_one({"title": title, "photo_id": photo_id, "file_id": file.file_id, "file_type": ftype, "clicks": 0, "duration": duration_str, "created_at": datetime.datetime.utcnow()})
                me = await bot.get_me()
                kb = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="🎬 মুভিটি দেখুন", url=f"https://t.me/{me.username}?start=new")]])
                await bot.send_photo(CHANNEL_ID, photo=photo_id, caption=f"🎥 <b>নতুন মুভি যুক্ত হয়েছে!</b>\n\n🎬 নাম: <b>{title}</b>", parse_mode="HTML", reply_markup=kb)
                await status_msg.edit_text(f"🎉 <b>{title}</b> সফলভাবে যুক্ত হয়েছে!")
            
            if os.path.exists(video_path): os.remove(video_path)
            if os.path.exists(grid_path): os.remove(grid_path)
            if uid in admin_temp: del admin_temp[uid]
        except Exception as e: await status_msg.edit_text(f"❌ এরর: {e}")

# ==========================================
# ৪. ওয়েব অ্যাপ UI
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def web_ui():
    ad_cfg = await db.settings.find_one({"id": "ad_config"})
    tg_cfg = await db.settings.find_one({"id": "link_tg"})
    b18_cfg = await db.settings.find_one({"id": "link_18"})
    sn_cfg = await db.settings.find_one({"id": "site_name"})
    nt_cfg = await db.settings.find_one({"id": "site_notice"})
    st_cfg = await db.settings.find_one({"id": "step_config"})
    h_ad = await db.settings.find_one({"id": "header_ad"})
    m_ad = await db.settings.find_one({"id": "middle_ad"})
    f_ad = await db.settings.find_one({"id": "footer_ad"})
    monetag_cfg = await db.settings.find_one({"id": "monetag_status"})
    adlink_cfg = await db.settings.find_one({"id": "adlink_status"})
    all_links = await db.ad_links.find().to_list(length=100)
    direct_links = [l['url'] for l in all_links] if all_links else []
    
    zone_id = ad_cfg['zone_id'] if ad_cfg else "10916755"
    site_name = sn_cfg['name'] if sn_cfg else "MovieZone"
    notice_text = nt_cfg['text'] if nt_cfg else "মুভি অ্যাপে স্বাগতম!"
    total_steps = st_cfg['count'] if st_cfg else 2
    header_code = h_ad['code'] if h_ad else ""
    middle_code = m_ad['code'] if m_ad else ""
    footer_code = f_ad['code'] if f_ad else ""
    monetag_on = monetag_cfg['status'] if monetag_cfg else True
    adlink_on = adlink_cfg['status'] if adlink_cfg else False

    html_code = r"""
    <!DOCTYPE html>
    <html lang="bn">
    <head>
        <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{{SITE_NAME}}</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
        <style>
            * { margin:0; padding:0; box-sizing:border-box; }
            body { background:#0f172a; font-family: sans-serif; color:#fff; overflow-x:hidden; } 
            .main-container { max-width: 600px; margin: 0 auto; background: #0f172a; min-height: 100vh; }
            header { display:flex; justify-content:space-between; align-items:center; padding:15px; border-bottom:1px solid #1e293b; background:#0f172a; position:sticky; top:0; z-index:1000; }
            .logo { font-size:22px; font-weight:bold; }
            .notice-bar { background: red; color: white; padding: 10px; font-size: 14px; text-align: center; }
            .ad-slot { width: 100%; display: flex; justify-content: center; padding: 10px 0; }
            .search-box { padding:15px; }
            .search-input { width: 100%; padding:14px; border-radius:25px; border:none; text-align:center; background:#1e293b; color:#fff; font-size:16px; }
            
            /* ৪ কলাম গ্রিড লেআউট */
            .grid { 
                padding:0 8px 120px; 
                display: grid; 
                grid-template-columns: repeat(4, 1fr); 
                gap: 5px; 
            }
            .card { background:#1e293b; border-radius:8px; overflow:hidden; cursor:pointer; position: relative; border: 1px solid #334155;}
            .post-content img { width:100%; height:110px; object-fit:cover; display:block; border-radius: 5px; }
            
            .duration-badge { position: absolute; top: 5px; left: 5px; background: rgba(0,0,0,0.8); color: #fff; padding: 2px 4px; border-radius: 4px; font-size: 8px; z-index: 10; }
            .tag { position:absolute; top:5px; right:5px; font-size: 8px; padding: 2px 4px; border-radius: 4px; z-index: 10;}
            .tag-locked { background:rgba(0,0,0,0.85); color:#f87171; border: 1px solid #f87171; }
            .tag-unlocked { background:rgba(0,0,0,0.85); color:#10b981; border: 1px solid #10b981; }
            .view-badge { position:absolute; bottom:5px; left:5px; background:rgba(0,0,0,0.7); color:#fff; padding: 2px 4px; border-radius: 4px; font-size: 8px; }

            .card-footer { padding:5px 2px; font-size:9px; font-weight:bold; text-align:center; color:#e2e8f0; height: 32px; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;}
            
            .ad-screen { position:fixed; top:0; left:0; width:100%; height:100%; background:#0f172a; display:none; flex-direction:column; align-items:center; justify-content:center; z-index:2000; padding: 20px; }
            .timer { width:80px; height:80px; border-radius:50%; border:5px solid red; display:flex; align-items:center; justify-content:center; font-size:30px; margin-bottom:20px; color:red; font-weight:bold; }
            .btn-visit { background:#f87171; color:white; padding:15px 30px; border-radius:30px; font-size:18px; font-weight:bold; cursor:pointer; width:80%; text-align:center; text-decoration:none; display:block; }
            .btn-next { background:#10b981; color:white; padding:15px 30px; border-radius:30px; font-size:18px; font-weight:bold; cursor:pointer; display:none; width:80%; }
            .modal { position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.8); display:none; align-items:center; justify-content:center; z-index:3000; }
            .modal-content { background:#1e293b; width:90%; padding:30px; border-radius:15px; text-align:center; }
        </style>
    </head>
    <body>
        <div class="main-container">
            <header><div class="logo">{{SITE_NAME}}</div></header>
            <div class="notice-bar">{{NOTICE_TEXT}}</div>
            <div class="ad-slot">{{HEADER_AD}}</div>
            <div class="search-box"><input type="text" id="searchInput" class="search-input" placeholder="মুভি খুঁজুন..."></div>
            <div class="ad-slot">{{MIDDLE_AD}}</div>
            <div class="grid" id="movieGrid"></div>
            <div class="ad-slot">{{FOOTER_AD}}</div>
        </div>

        <div id="adScreen" class="ad-screen">
            <div class="timer" id="timer">0</div>
            <p id="adMsg" style="text-align:center; margin-bottom:15px; color:#fff;">অ্যাড ভিজিট করুন এবং ১৫ সেকেন্ড অপেক্ষা করুন।</p>
            <a href="#" target="_blank" class="btn-visit" id="btnVisit" onclick="startTimer()">Visit Ad & Unlock</a>
            <button class="btn-next" id="btnNext" onclick="handleNextStep()">Next Step</button>
        </div>

        <div id="successModal" class="modal"><div class="modal-content"><h2>সম্পন্ন হয়েছে!</h2><p>বটের ইনবক্স চেক করুন।</p><button style="margin-top:10px; padding:10px 20px; background:#10b981; border:none; color:#fff; border-radius:5px;" onclick="tg.close()">বটে ফিরে যান</button></div></div>

        <script>
            let tg = window.Telegram.WebApp; tg.expand();
            const ZONE_ID = "10916755";
            const MONETAG_ON = {{MONETAG_ON}};
            const ADLINK_ON = {{ADLINK_ON}};
            const DIRECT_LINKS = {{DIRECT_LINKS}};
            const TOTAL_STEPS = {{TOTAL_STEPS}};
            let uid = tg.initDataUnsafe.user?.id || 0;
            let currentAdStep = 1; let currentMovieId = "";

            async function loadMovies(q = "") {
                const grid = document.getElementById('movieGrid');
                const r = await fetch(`/api/list?q=${q}&uid=${uid}`);
                const data = await r.json();
                grid.innerHTML = data.movies.map(m => `
                    <div class="card" onclick="handleMovieClick('${m._id}', ${m.is_unlocked}, '${m.photo_id}')">
                        <div class="post-content">
                            <div class="duration-badge">${m.duration || '0m'}</div>
                            <img src="/api/image/${m.photo_id}" onerror="this.src='https://via.placeholder.com/200x110'">
                            <div class="tag ${m.is_unlocked ? 'tag-unlocked' : 'tag-locked'}">${m.is_unlocked ? '🔓' : '🔒'}</div>
                            <div class="view-badge">👁 ${m.clicks}</div>
                        </div>
                        <div class="card-footer">${m.title}</div>
                    </div>`).join('');
            }

            function handleMovieClick(id, isUnlocked, photoId) {
                if(isUnlocked) sendFile(id);
                else { currentMovieId = id; currentAdStep = 1; document.getElementById('adScreen').style.display = 'flex'; showAdScreen(); }
            }

            function showAdScreen() {
                document.getElementById('btnVisit').style.display = 'block';
                document.getElementById('btnNext').style.display = 'none';
                document.getElementById('timer').innerText = "15";
                let link = DIRECT_LINKS[Math.floor(Math.random() * DIRECT_LINKS.length)] || "#";
                document.getElementById('btnVisit').href = link;
            }

            function startTimer() {
                let t = 15; document.getElementById('btnVisit').style.pointerEvents = 'none';
                let iv = setInterval(() => {
                    t--; document.getElementById('timer').innerText = t;
                    if(t <= 0) { clearInterval(iv); document.getElementById('btnVisit').style.display = 'none'; document.getElementById('btnNext').style.display = 'block'; }
                }, 1000);
            }

            function handleNextStep() {
                if(currentAdStep < TOTAL_STEPS) { currentAdStep++; showAdScreen(); }
                else sendFile(currentMovieId);
            }

            async function sendFile(id) {
                await fetch('/api/send', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({userId: uid, movieId: id})});
                document.getElementById('adScreen').style.display = 'none';
                document.getElementById('successModal').style.display = 'flex';
            }

            document.getElementById('searchInput').addEventListener('input', e => loadMovies(e.target.value));
            loadMovies();
        </script>
    </body>
    </html>
    """
    final_html = html_code.replace("{{SITE_NAME}}", site_name).replace("{{NOTICE_TEXT}}", notice_text).replace("{{HEADER_AD}}", header_code).replace("{{MIDDLE_AD}}", middle_code).replace("{{FOOTER_AD}}", footer_code)
    final_html = final_html.replace("{{MONETAG_ON}}", "true" if monetag_on else "false").replace("{{ADLINK_ON}}", "true" if adlink_on else "false").replace("{{DIRECT_LINKS}}", str(direct_links)).replace("{{TOTAL_STEPS}}", str(total_steps))
    return final_html

# ==========================================
# ৫. এপিআই সেকশন (pagination বাদ দিয়ে সব মুভি একবারে আসবে)
# ==========================================

@app.get("/api/list")
async def list_movies(q: str = "", uid: int = 0):
    query = {"title": {"$regex": q, "$options": "i"}} if q else {}
    unlock_cfg = await db.settings.find_one({"id": "unlock_config"})
    u_seconds = unlock_cfg.get('seconds', 86400) if unlock_cfg else 86400
    unlocked_map = {}
    if uid != 0:
        time_limit = datetime.datetime.utcnow() - datetime.timedelta(seconds=u_seconds)
        async for u in db.user_unlocks.find({"user_id": uid, "unlocked_at": {"$gt": time_limit}}):
            unlocked_map[u["movie_id"]] = True
    movies = []
    # এখানে LIMIT বাদ দেওয়া হয়েছে যাতে সব মুভি একসাথে লোড হয়
    async for m in db.movies.find(query).sort("created_at", -1):
        m_id = str(m["_id"]); m["_id"] = m_id
        m["is_unlocked"] = m_id in unlocked_map 
        movies.append(m)
    return {"movies": movies}

@app.get("/api/image/{photo_id}")
async def get_image(photo_id: str):
    try:
        file_info = await bot.get_file(photo_id)
        file_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}"
        async def stream_image():
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as resp:
                    async for chunk in resp.content.iter_chunked(1024): yield chunk
        return StreamingResponse(stream_image(), media_type="image/jpeg")
    except: return {"error": "not found"}

@app.post("/api/send")
async def send_file(d: dict = Body(...)):
    uid, mid = d['userId'], d['movieId']
    try:
        m = await db.movies.find_one({"_id": ObjectId(mid)})
        if m:
            time_cfg = await db.settings.find_one({"id": "del_time"})
            del_min = time_cfg['minutes'] if time_cfg else 60
            sent_msg = await bot.send_video(uid, m['file_id'], caption=f"🎥 {m['title']}\n⏳ {del_min}m পর ডিলিট হবে।", parse_mode="HTML")
            await db.movies.update_one({"_id": ObjectId(mid)}, {"$inc": {"clicks": 1}})
            await db.user_unlocks.update_one({"user_id": uid, "movie_id": mid}, {"$set": {"unlocked_at": datetime.datetime.utcnow()}}, upsert=True)
            if sent_msg:
                del_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=del_min)
                await db.auto_delete.insert_one({"chat_id": uid, "message_id": sent_msg.message_id, "delete_at": del_at})
    except: pass
    return {"ok": True}

async def start():
    await load_admins(); await pyro_bot.start()
    port = int(os.getenv("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, loop="asyncio")
    server = uvicorn.Server(config)
    asyncio.create_task(auto_delete_worker())
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.gather(server.serve(), dp.start_polling(bot))

if __name__ == "__main__": asyncio.run(start())
