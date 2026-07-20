import os
import json
import logging
import time
import re
import asyncio
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from geopy.distance import geodesic

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("❌ Token bot tidak ditemukan di .env")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(BASE_DIR, "gym_data.json")
RATINGS_PATH = os.path.join(BASE_DIR, "ratings.json")
IMAGE_DIR = os.path.join(BASE_DIR, "images")
os.makedirs(IMAGE_DIR, exist_ok=True)

# Load data gym
try:
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        GYM_DATA = json.load(f)
    for gym in GYM_DATA:
        gym.setdefault("status", "approved")
        gym.setdefault("gambar", "")
        gym.setdefault("hp_wa", "")
        gym.setdefault("medsos", "")
        gym.setdefault("link_maps", "")
        gym.setdefault("link_drive", "")
        gym.setdefault("rating", {"total": 0, "count": 0, "ratings": []})
        gym.setdefault("latitude", None)
        gym.setdefault("longitude", None)
    logger.info(f"✅ Data gym dimuat: {len(GYM_DATA)} item")
except:
    GYM_DATA = []
    logger.warning("⚠️ File gym_data.json tidak ditemukan, memulai kosong.")

# Load ratings data
try:
    with open(RATINGS_PATH, "r", encoding="utf-8") as f:
        RATINGS_DATA = json.load(f)
    logger.info(f"✅ Data rating dimuat: {len(RATINGS_DATA)} item")
except:
    RATINGS_DATA = {}
    logger.warning("⚠️ File ratings.json tidak ditemukan, memulai kosong.")

def save_gym_data():
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(GYM_DATA, f, indent=2, ensure_ascii=False)

def save_ratings_data():
    with open(RATINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(RATINGS_DATA, f, indent=2, ensure_ascii=False)

def get_image_path(filename):
    return os.path.join(IMAGE_DIR, filename)

def is_admin(user_id):
    return user_id == ADMIN_ID

def escape_markdown(text):
    """Escape karakter khusus Markdown"""
    if not isinstance(text, str):
        return str(text)
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

def is_valid_url(url):
    """Validasi URL yang valid untuk Telegram"""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if not url:
        return False
    if not url.startswith(('http://', 'https://')):
        return False
    parts = url.split('://')
    if len(parts) != 2:
        return False
    domain = parts[1]
    if not domain or len(domain) < 3:
        return False
    if domain in ['@', '@gyghmn', 'gyghmn']:
        return False
    if '.' not in domain and not any(c.isalpha() for c in domain):
        return False
    return True

def format_medsos(medsos):
    """Format medsos agar valid untuk URL"""
    if not medsos or not isinstance(medsos, str):
        return ""
    medsos = medsos.strip()
    if not medsos:
        return ""
    if medsos.startswith("@"):
        medsos = medsos[1:]
    if medsos.startswith(('http://', 'https://')):
        return medsos
    return "https://" + medsos

def format_phone(phone):
    if not phone:
        return ""
    digits = re.sub(r'\D', '', phone)
    return digits

def get_gym_rating(gym_id):
    """Mendapatkan rating untuk gym tertentu"""
    gym_id_str = str(gym_id)
    if gym_id_str in RATINGS_DATA:
        ratings = RATINGS_DATA[gym_id_str]
        if ratings:
            avg = sum(ratings) / len(ratings)
            return {
                "average": round(avg, 1),
                "count": len(ratings),
                "ratings": ratings
            }
    return {"average": 0, "count": 0, "ratings": []}

def add_rating(gym_id, user_id, rating_value):
    """Menambahkan rating untuk gym"""
    gym_id_str = str(gym_id)
    
    if gym_id_str not in RATINGS_DATA:
        RATINGS_DATA[gym_id_str] = []
    
    RATINGS_DATA[gym_id_str].append(rating_value)
    save_ratings_data()
    
    return get_gym_rating(gym_id)

def get_rating_stars(rating):
    """Mengubah rating angka menjadi bintang"""
    if rating == 0:
        return "⭐" * 5 + " (Belum ada rating)"
    full_stars = int(rating)
    empty_stars = 5 - full_stars
    return "⭐" * full_stars + "☆" * empty_stars + f" ({rating})"

def sort_gyms_by_rating(gyms):
    """Mengurutkan gym berdasarkan rating tertinggi"""
    def get_rating_value(gym):
        rating_info = get_gym_rating(gym['id'])
        return rating_info['average']
    
    return sorted(gyms, key=get_rating_value, reverse=True)

def calculate_distance(lat1, lon1, lat2, lon2):
    """Menghitung jarak antara dua titik koordinat dalam kilometer"""
    if not lat1 or not lon1 or not lat2 or not lon2:
        return None
    try:
        return geodesic((lat1, lon1), (lat2, lon2)).kilometers
    except:
        return None

def format_distance(distance_km):
    """Format jarak menjadi meter atau kilometer"""
    if distance_km is None:
        return "Tidak diketahui"
    if distance_km < 1:
        return f"{int(distance_km * 1000)} meter"
    return f"{distance_km:.1f} km"

# ==================== STATE UNTUK CONVERSATION ====================
UPLOAD_NAME, UPLOAD_LOCATION, UPLOAD_HP, UPLOAD_MEDSOS, UPLOAD_MAPS, UPLOAD_DRIVE, UPLOAD_PHOTO, UPLOAD_CONFIRM = range(20, 28)

user_sessions = {}
upload_temp = {}

async def delete_message_safe(context, chat_id, message_id):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.warning(f"Gagal hapus pesan: {e}")

async def safe_answer(query, text=None, show_alert=False):
    try:
        if query:
            await query.answer(text, show_alert=show_alert)
    except:
        pass

async def clear_all_messages(context, chat_id, user_id):
    """Menghapus semua pesan yang tersimpan untuk user"""
    try:
        if user_id in user_sessions:
            session = user_sessions[user_id]
            if session.get("message_ids"):
                for msg_id in session["message_ids"]:
                    await delete_message_safe(context, chat_id, msg_id)
            if session.get("message_id"):
                await delete_message_safe(context, chat_id, session["message_id"])
            user_sessions.pop(user_id, None)
        
        if user_id in upload_temp:
            if upload_temp[user_id].get("gambar"):
                image_path = get_image_path(upload_temp[user_id]["gambar"])
                if os.path.exists(image_path):
                    try:
                        os.remove(image_path)
                    except:
                        pass
            upload_temp.pop(user_id, None)
            
    except Exception as e:
        logger.warning(f"Gagal membersihkan pesan: {e}")

async def reset_upload_session(user_id):
    """Reset session upload"""
    if user_id in upload_temp:
        if upload_temp[user_id].get("gambar"):
            image_path = get_image_path(upload_temp[user_id]["gambar"])
            if os.path.exists(image_path):
                try:
                    os.remove(image_path)
                except:
                    pass
        upload_temp.pop(user_id, None)

async def save_message_id(user_id, message_id):
    """Menyimpan message_id untuk dihapus nanti"""
    if user_id not in user_sessions:
        user_sessions[user_id] = {"message_ids": []}
    if "message_ids" not in user_sessions[user_id]:
        user_sessions[user_id]["message_ids"] = []
    user_sessions[user_id]["message_ids"].append(message_id)

def build_gym_caption(gym, index, total, user_location=None):
    rating_info = get_gym_rating(gym['id'])
    rating_display = get_rating_stars(rating_info['average'])
    
    caption = (
        f"🏋️ *{escape_markdown(gym['nama'])}*\n\n"
        f"📍 Lokasi: {escape_markdown(gym['lokasi'])}\n"
        f"📞 HP/WA: {escape_markdown(gym['hp_wa'])}\n"
        f"🌐 Medsos: {escape_markdown(gym['medsos'])}\n"
        f"🗺️ [Google Maps]({gym['link_maps']})\n"
        f"📁 [Drive]({gym['link_drive']})\n\n"
        f"⭐ Rating: {rating_display}\n"
        f"({rating_info['count']} orang memberi rating)\n"
    )
    
    if user_location and gym.get('latitude') and gym.get('longitude'):
        distance = calculate_distance(
            user_location[0], user_location[1],
            gym['latitude'], gym['longitude']
        )
        if distance is not None:
            caption += f"\n📍 Jarak: {format_distance(distance)}"
    
    caption += f"\n\n🏆 Peringkat: #{index+1} dari {total} (berdasarkan rating)\n\n"
    caption += f"Gym {index+1} dari {total}"
    
    return caption

def build_detail_keyboard(index, total, gym, user_location=None):
    buttons = []
    nav_row = []
    if index > 0:
        nav_row.append(InlineKeyboardButton("◀️ Sebelumnya", callback_data="nav_prev"))
    nav_row.append(InlineKeyboardButton("❌ Tutup", callback_data="nav_close"))
    if index < total - 1:
        nav_row.append(InlineKeyboardButton("▶️ Berikutnya", callback_data="nav_next"))
    buttons.append(nav_row)

    if gym.get("hp_wa"):
        wa_digits = format_phone(gym['hp_wa'])
        if wa_digits and len(wa_digits) >= 5:
            wa_link = f"https://wa.me/{wa_digits}"
            buttons.append([InlineKeyboardButton("📞 Hubungi Admin", url=wa_link)])
        else:
            buttons.append([InlineKeyboardButton("📞 Hubungi Admin", callback_data="no_contact")])
    else:
        buttons.append([InlineKeyboardButton("📞 Hubungi Admin", callback_data="no_contact")])
    
    medsos = gym.get("medsos", "")
    medsos_url = format_medsos(medsos)
    
    if medsos_url and is_valid_url(medsos_url):
        buttons.append([InlineKeyboardButton("🌐 Medsos", url=medsos_url)])
    else:
        buttons.append([InlineKeyboardButton("🌐 Medsos", callback_data="no_medsos")])

    if user_location and gym.get('latitude') and gym.get('longitude'):
        maps_url = f"https://www.google.com/maps/dir/{user_location[0]},{user_location[1]}/{gym['latitude']},{gym['longitude']}"
        buttons.append([InlineKeyboardButton("🗺️ Navigasi ke Gym", url=maps_url)])

    buttons.append([InlineKeyboardButton("⭐ Beri Rating", callback_data=f"show_rating_{gym['id']}")])
    buttons.append([InlineKeyboardButton("🔙 Kembali ke Daftar", callback_data="back_to_list")])
    return InlineKeyboardMarkup(buttons)

# ==================== WELCOME PAGE ====================
async def welcome_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan halaman selamat datang saat user menekan /start"""
    
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    # Reset upload session
    await reset_upload_session(user_id)
    
    # Hapus semua pesan sebelumnya
    await clear_all_messages(context, chat_id, user_id)
    
    # Hapus pesan /start yang dikirim user
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
    except Exception as e:
        logger.warning(f"Gagal hapus pesan start: {e}")
    
    welcome_text = (
        "*GYM SCOUT* 💪\n\n"
        "Temukan *Gym Terbaik di Malang*!\n\n"
        "• Rekomendasi gym terpercaya & terbaru\n"
        "• Filter berdasarkan lokasi\n"
        "• Review jujur dari pengguna lain\n"
        "• Cari gym terdekat dari lokasi kamu\n\n"
        "💡 *Mulai Sekarang* dan temukan gym yang cocok untukmu!"
    )
    
    keyboard = [
        [InlineKeyboardButton("🚀 Mulai Sekarang", callback_data="main")]
    ]
    reply = InlineKeyboardMarkup(keyboard)
    
    possible_logo_names = ["logo.jpg", "logo.png", "logo.jpeg", "Logo.jpg", "Logo.png"]
    image_to_send = None
    
    for name in possible_logo_names:
        path = get_image_path(name)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            image_to_send = path
            break
    
    if image_to_send:
        try:
            with open(image_to_send, "rb") as photo:
                msg = await update.message.reply_photo(
                    photo=InputFile(photo),
                    caption=welcome_text,
                    reply_markup=reply,
                    parse_mode="Markdown"
                )
                await save_message_id(user_id, msg.message_id)
        except Exception as e:
            logger.error(f"Error sending welcome photo: {e}")
            msg = await update.message.reply_text(
                welcome_text,
                reply_markup=reply,
                parse_mode="Markdown"
            )
            await save_message_id(user_id, msg.message_id)
    else:
        msg = await update.message.reply_text(
            welcome_text,
            reply_markup=reply,
            parse_mode="Markdown"
        )
        await save_message_id(user_id, msg.message_id)

# ==================== MENU UTAMA ====================
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan menu utama"""
    
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Reset upload session
    await reset_upload_session(user_id)
    
    if query:
        await safe_answer(query)
        
        # Hapus semua pesan sebelumnya
        await clear_all_messages(context, chat_id, user_id)
        
        # Hapus pesan query
        if query.message:
            await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    
    keyboard = [
        [InlineKeyboardButton("📍 Gym Terdekat", callback_data="menu_lokasi")],
        [InlineKeyboardButton("🔍 Cari Gym", callback_data="menu_cari")],
        [InlineKeyboardButton("📤 Upload Gym Baru", callback_data="menu_upload")],
    ]
    if is_admin(update.effective_user.id):
        keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    reply = InlineKeyboardMarkup(keyboard)
    text = "🏠 **Menu Utama**\nPilih menu:"
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)

# ==================== HANDLE TEXT MESSAGE ====================
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pesan teks biasa yang dikirim user tanpa menekan button"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Cek apakah user sedang dalam proses upload
    if user_id in upload_temp:
        # Biarkan ConversationHandler yang menangani
        return
    
    # Hapus pesan teks yang dikirim user
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
    except Exception as e:
        logger.warning(f"Gagal hapus pesan teks: {e}")
    
    # Hapus semua pesan sebelumnya di session
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if session.get("message_ids"):
            for msg_id in session["message_ids"]:
                await delete_message_safe(context, chat_id, msg_id)
            session["message_ids"] = []
        if session.get("message_id"):
            await delete_message_safe(context, chat_id, session["message_id"])
        user_sessions.pop(user_id, None)
    
    # Kirim pesan error dengan menu
    keyboard = [
        [InlineKeyboardButton("📍 Gym Terdekat", callback_data="menu_lokasi")],
        [InlineKeyboardButton("🔍 Cari Gym", callback_data="menu_cari")],
        [InlineKeyboardButton("📤 Upload Gym Baru", callback_data="menu_upload")],
    ]
    if is_admin(update.effective_user.id):
        keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    reply = InlineKeyboardMarkup(keyboard)
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="❌ Mohon maaf, bot ini hanya dapat berinteraksi melalui tombol menu yang tersedia.\n\n"
             "Silakan gunakan tombol di bawah ini untuk memulai:",
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)

# ==================== MENU LOKASI TERDEKAT ====================
async def menu_lokasi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menu untuk mencari gym terdekat dari lokasi user"""
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await safe_answer(query)
    
    # Hapus semua pesan sebelumnya
    await clear_all_messages(context, chat_id, user_id)
    
    # Hapus pesan query
    if query and query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    
    # Cek apakah user sudah pernah mengirim lokasi sebelumnya
    user_location = context.user_data.get('user_location')
    
    if user_location:
        # Jika sudah ada lokasi, tampilkan opsi
        keyboard = [
            [InlineKeyboardButton("📍 Cari Gym Terdekat", callback_data="cari_dengan_lokasi_tersimpan")],
            [InlineKeyboardButton("🔄 Ganti Lokasi", callback_data="kirim_lokasi")],
            [InlineKeyboardButton("🔙 Kembali", callback_data="main")]
        ]
        reply = InlineKeyboardMarkup(keyboard)
        
        text = (
            "📍 **Cari Gym Terdekat**\n\n"
            f"📍 Lokasi tersimpan: *{user_location[0]:.6f}, {user_location[1]:.6f}*\n\n"
            "Klik 'Cari Gym Terdekat' untuk mencari gym terdekat dari lokasi yang tersimpan.\n"
            "Atau klik 'Ganti Lokasi' untuk mengirim lokasi baru."
        )
        
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply,
            parse_mode="Markdown"
        )
        await save_message_id(user_id, msg.message_id)
        return
    
    # Jika belum ada lokasi, tampilkan form untuk kirim lokasi
    keyboard = [
        [InlineKeyboardButton("📍 Kirim Lokasi Saya", callback_data="kirim_lokasi")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="main")]
    ]
    reply = InlineKeyboardMarkup(keyboard)
    
    text = (
        "📍 **Cari Gym Terdekat**\n\n"
        "Untuk mencari gym terdekat dari lokasi Anda, silakan kirim lokasi Anda.\n\n"
        "Cara mengirim lokasi:\n"
        "1. Klik tombol 📎 (lampiran)\n"
        "2. Pilih 📍 Lokasi\n"
        "3. Kirim lokasi Anda\n\n"
        "Atau klik tombol di bawah untuk mengirim lokasi."
    )
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)

async def show_nearest_gyms(update: Update, context: ContextTypes.DEFAULT_TYPE, user_location):
    """Menampilkan gym terdekat berdasarkan lokasi yang tersimpan"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Hapus semua pesan sebelumnya
    await clear_all_messages(context, chat_id, user_id)
    
    user_lat, user_lon = user_location
    
    gyms_with_location = []
    for gym in GYM_DATA:
        if gym.get("status") == "approved" and gym.get("latitude") and gym.get("longitude"):
            distance = calculate_distance(
                user_lat, user_lon,
                gym['latitude'], gym['longitude']
            )
            if distance is not None:
                gyms_with_location.append({
                    "gym": gym,
                    "distance": distance
                })
    
    if not gyms_with_location:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Maaf, belum ada gym yang memiliki data lokasi.\n\n"
                 "Admin perlu menambahkan koordinat untuk setiap gym.\n"
                 "Silakan coba fitur lain atau hubungi admin."
        )
        await save_message_id(user_id, msg.message_id)
        return
    
    gyms_with_location.sort(key=lambda x: x["distance"])
    
    user_sessions[user_id] = {
        "list": [item["gym"] for item in gyms_with_location],
        "index": 0,
        "user_location": (user_lat, user_lon),
        "mode": "terdekat",
        "message_ids": []
    }
    
    await tampilkan_gym(update, context, user_id)

async def cari_dengan_lokasi_tersimpan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mencari gym terdekat dengan lokasi yang sudah tersimpan"""
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await safe_answer(query)
    
    # Hapus semua pesan sebelumnya
    await clear_all_messages(context, chat_id, user_id)
    
    # Hapus pesan query
    if query and query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    
    user_location = context.user_data.get('user_location')
    
    if not user_location:
        await safe_answer(query, "❌ Tidak ada lokasi tersimpan. Silakan kirim lokasi terlebih dahulu.", show_alert=True)
        await menu_lokasi(update, context)
        return
    
    await show_nearest_gyms(update, context, user_location)

async def kirim_lokasi_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt untuk meminta user mengirim lokasi"""
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await safe_answer(query)
    
    # Hapus semua pesan sebelumnya
    await clear_all_messages(context, chat_id, user_id)
    
    # Hapus pesan query
    if query and query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    
    text = (
        "📍 **Kirim Lokasi Anda**\n\n"
        "Silakan kirim lokasi Anda dengan cara:\n"
        "1. Klik tombol 📎 (lampiran)\n"
        "2. Pilih 📍 Lokasi\n"
        "3. Kirim lokasi Anda\n\n"
        "Atau Anda bisa langsung mengirim lokasi dari chat.\n\n"
        "*Lokasi akan disimpan untuk penggunaan berikutnya.*"
    )
    
    keyboard = [[InlineKeyboardButton("🔙 Kembali", callback_data="menu_lokasi")]]
    reply = InlineKeyboardMarkup(keyboard)
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle lokasi yang dikirim user"""
    user_location = update.message.location
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if not user_location:
        await update.message.reply_text("❌ Mohon kirim lokasi yang valid.")
        return
    
    user_lat = user_location.latitude
    user_lon = user_location.longitude
    
    # Simpan lokasi user di context untuk digunakan kembali
    context.user_data['user_location'] = (user_lat, user_lon)
    
    logger.info(f"📍 User location saved: {user_lat}, {user_lon}")
    
    # Hapus pesan lokasi yang dikirim user
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
    except:
        pass
    
    msg = await update.message.reply_text("✅ Lokasi berhasil disimpan! Mencari gym terdekat...")
    await save_message_id(user_id, msg.message_id)
    
    await show_nearest_gyms(update, context, (user_lat, user_lon))

# ==================== CARI GYM ====================
async def menu_cari(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await safe_answer(query)
    
    # Hapus semua pesan sebelumnya
    await clear_all_messages(context, chat_id, user_id)
    
    # Hapus pesan query
    if query and query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    
    lokasi_set = sorted({g["lokasi"] for g in GYM_DATA if g.get("status") == "approved" and g.get("lokasi")})
    if not lokasi_set:
        keyboard = [[InlineKeyboardButton("🔙 Kembali", callback_data="main")]]
        reply = InlineKeyboardMarkup(keyboard)
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Belum ada gym yang disetujui.",
            reply_markup=reply
        )
        await save_message_id(user_id, msg.message_id)
        return

    keyboard = [
        [InlineKeyboardButton("🏆 Rekomendasi Terbaik", callback_data="rekomendasi")],
        [InlineKeyboardButton("📍 Cari Berdasarkan Wilayah", callback_data="cari_wilayah")]
    ]
    keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="main")])
    reply = InlineKeyboardMarkup(keyboard)
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="📍 **Cari Gym**\n\nPilih mode pencarian:",
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)

async def cari_wilayah(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await safe_answer(query)
    
    # Hapus semua pesan sebelumnya
    await clear_all_messages(context, chat_id, user_id)
    
    # Hapus pesan query
    if query and query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    
    lokasi_set = sorted({g["lokasi"] for g in GYM_DATA if g.get("status") == "approved" and g.get("lokasi")})
    if not lokasi_set:
        await safe_answer(query, "Belum ada gym.", show_alert=True)
        return
    
    keyboard = [[InlineKeyboardButton(loc.title(), callback_data=f"wilayah_{loc}")] for loc in lokasi_set]
    keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="menu_cari")])
    reply = InlineKeyboardMarkup(keyboard)
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="📍 **Pilih Wilayah Gym**",
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)

async def rekomendasi_gym(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await safe_answer(query)
    
    # Hapus semua pesan sebelumnya
    await clear_all_messages(context, chat_id, user_id)
    
    # Hapus pesan query
    if query and query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    
    all_gyms = [g for g in GYM_DATA if g.get("status") == "approved"]
    
    if not all_gyms:
        await safe_answer(query, "Belum ada gym yang tersedia.", show_alert=True)
        return
    
    sorted_gyms = sort_gyms_by_rating(all_gyms)
    
    user_sessions[user_id] = {"list": sorted_gyms, "index": 0, "mode": "rekomendasi", "message_ids": []}
    await tampilkan_gym(update, context, user_id)

async def pilih_wilayah(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await safe_answer(query)
    
    # Hapus semua pesan sebelumnya
    await clear_all_messages(context, chat_id, user_id)
    
    # Hapus pesan query
    if query and query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    
    wilayah = query.data.split("_", 1)[1]
    results = [g for g in GYM_DATA if g["lokasi"].lower() == wilayah.lower() and g.get("status") == "approved"]
    if not results:
        await safe_answer(query, "Tidak ada gym di wilayah ini.", show_alert=True)
        return
    
    sorted_results = sort_gyms_by_rating(results)
    
    user_sessions[user_id] = {"list": sorted_results, "index": 0, "mode": "wilayah", "message_ids": []}
    await tampilkan_gym(update, context, user_id)

async def tampilkan_gym(update, context, user_id):
    session = user_sessions.get(user_id)
    if not session:
        return
    
    chat_id = update.effective_chat.id
    
    # Hapus semua pesan sebelumnya di session
    if session.get("message_ids"):
        for msg_id in session["message_ids"]:
            await delete_message_safe(context, chat_id, msg_id)
        session["message_ids"] = []
    
    index = session["index"]
    gym = session["list"][index]
    user_location = session.get("user_location")
    
    caption = build_gym_caption(gym, index, len(session["list"]), user_location)
    
    try:
        reply = build_detail_keyboard(index, len(session["list"]), gym, user_location)
    except Exception as e:
        logger.error(f"Error building keyboard: {e}")
        reply = InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Sebelumnya", callback_data="nav_prev"),
             InlineKeyboardButton("❌ Tutup", callback_data="nav_close"),
             InlineKeyboardButton("▶️ Berikutnya", callback_data="nav_next")],
            [InlineKeyboardButton("🔙 Kembali ke Menu", callback_data="main")]
        ])

    # Hapus pesan query jika ada
    if update.callback_query and update.callback_query.message:
        await delete_message_safe(context, chat_id, update.callback_query.message.message_id)

    image_path = get_image_path(gym.get("gambar", ""))
    
    try:
        if os.path.exists(image_path) and os.path.getsize(image_path) > 0:
            with open(image_path, "rb") as photo:
                msg = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=InputFile(photo),
                    caption=caption,
                    reply_markup=reply,
                    parse_mode="Markdown"
                )
        else:
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=f"{caption}\n\n⚠️ Gambar tidak tersedia",
                reply_markup=reply,
                parse_mode="Markdown"
            )
        session["message_ids"].append(msg.message_id)
        session["message_id"] = msg.message_id
    except Exception as e:
        logger.error(f"Error tampilkan gym: {e}")
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"{caption}\n\n⚠️ Error menampilkan gambar",
            reply_markup=reply,
            parse_mode="Markdown"
        )
        session["message_ids"].append(msg.message_id)
        session["message_id"] = msg.message_id

async def nav_slide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    session = user_sessions.get(user_id)
    
    if not session:
        await safe_answer(query, "Sesi habis. /start kembali.", show_alert=True)
        return
    
    await safe_answer(query)
    
    # Hapus pesan query
    if query and query.message:
        await delete_message_safe(context, chat_id, query.message.message_id)

    if data == "nav_close":
        await clear_all_messages(context, chat_id, user_id)
        await show_main_menu(update, context)
        return

    if data == "nav_prev":
        session["index"] -= 1
    elif data == "nav_next":
        session["index"] += 1
    else:
        return

    # Cek batas index
    if session["index"] < 0:
        session["index"] = 0
        if session.get("mode") == "terdekat":
            await safe_answer(query, "📍 Ini adalah gym terdekat dari lokasi Anda!", show_alert=True)
        else:
            await safe_answer(query, "📍 Ini adalah gym pertama!", show_alert=True)
        return
    elif session["index"] >= len(session["list"]):
        session["index"] = len(session["list"]) - 1
        if session.get("mode") == "terdekat":
            await safe_answer(query, "📍 Ini adalah gym terjauh yang tersedia!", show_alert=True)
        else:
            await safe_answer(query, "📍 Ini adalah gym terakhir!", show_alert=True)
        return

    await tampilkan_gym(update, context, user_id)

async def back_to_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    session = user_sessions.get(user_id)
    
    await safe_answer(query)
    
    # Hapus semua pesan
    await clear_all_messages(context, chat_id, user_id)
    
    if session:
        mode = session.get("mode", "default")
        if mode == "terdekat":
            await menu_lokasi(update, context)
        else:
            await menu_cari(update, context)
    else:
        await show_main_menu(update, context)

# ==================== RATING ====================
async def show_rating_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan menu rating"""
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    gym_id = int(query.data.split("_")[2])
    context.user_data['rating_gym_id'] = gym_id
    
    await safe_answer(query)
    
    # Hapus semua pesan sebelumnya
    await clear_all_messages(context, chat_id, user_id)
    
    rating_info = get_gym_rating(gym_id)
    current_rating = get_rating_stars(rating_info['average'])
    
    keyboard = [
        [
            InlineKeyboardButton("⭐ 1", callback_data="give_rating_1"),
            InlineKeyboardButton("⭐ 2", callback_data="give_rating_2"),
            InlineKeyboardButton("⭐ 3", callback_data="give_rating_3"),
            InlineKeyboardButton("⭐ 4", callback_data="give_rating_4"),
            InlineKeyboardButton("⭐ 5", callback_data="give_rating_5")
        ],
        [InlineKeyboardButton("🔙 Batal", callback_data="cancel_rating")]
    ]
    reply = InlineKeyboardMarkup(keyboard)
    
    message_text = (
        f"⭐ **Berikan Rating untuk gym ini**\n\n"
        f"Rating saat ini: {current_rating}\n"
        f"({rating_info['count']} orang memberi rating)\n\n"
        f"Pilih angka 1–5 untuk memberi rating:"
    )
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=message_text,
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)

async def proses_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Memproses rating yang diberikan user"""
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    rating_value = int(query.data.split("_")[2])
    gym_id = context.user_data.get('rating_gym_id')
    
    if not gym_id:
        await safe_answer(query, "Terjadi kesalahan. Silakan coba lagi.", show_alert=True)
        return
    
    await safe_answer(query)
    
    # Hapus pesan rating sebelumnya
    if query and query.message:
        await delete_message_safe(context, chat_id, query.message.message_id)
    
    rating_info = add_rating(gym_id, user_id, rating_value)
    rating_display = get_rating_stars(rating_info['average'])
    
    confirmation_text = (
        f"✅ **Rating berhasil disimpan!**\n\n"
        f"⭐ Rating Anda: {rating_value}/5\n"
        f"Rating rata-rata: {rating_display}\n"
        f"({rating_info['count']} orang memberi rating)\n\n"
        f"Terima kasih atas ratingnya! 🙏"
    )
    
    keyboard = [[InlineKeyboardButton("🔙 Kembali ke Gym", callback_data="back_to_gym")]]
    reply = InlineKeyboardMarkup(keyboard)
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=confirmation_text,
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)

async def back_to_gym_after_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    session = user_sessions.get(user_id)
    
    await safe_answer(query)
    
    # Hapus semua pesan
    await clear_all_messages(context, chat_id, user_id)
    
    if session:
        await tampilkan_gym(update, context, user_id)
    else:
        await show_main_menu(update, context)

async def cancel_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    session = user_sessions.get(user_id)
    
    await safe_answer(query, "Rating dibatalkan.")
    
    # Hapus semua pesan
    await clear_all_messages(context, chat_id, user_id)
    
    if session:
        await tampilkan_gym(update, context, user_id)
    else:
        await show_main_menu(update, context)

# ==================== UPLOAD GYM ====================
async def menu_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await safe_answer(query)
    
    # Hapus pesan query
    if query and query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    
    # Reset upload session jika ada
    await reset_upload_session(user_id)
    
    # Hapus semua pesan sebelumnya
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if session.get("message_ids"):
            for msg_id in session["message_ids"]:
                await delete_message_safe(context, chat_id, msg_id)
            session["message_ids"] = []
        if session.get("message_id"):
            await delete_message_safe(context, chat_id, session["message_id"])
        user_sessions.pop(user_id, None)
    
    # Buat session baru
    upload_temp[user_id] = {}
    
    keyboard = [
        [InlineKeyboardButton("❌ Batal", callback_data="cancel_upload")]
    ]
    reply = InlineKeyboardMarkup(keyboard)
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="📤 **Upload Gym Baru**\n\nKirim *nama gym*:",
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)
    return UPLOAD_NAME

async def upload_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Cek apakah session masih aktif
    if user_id not in upload_temp:
        await reset_upload_session(user_id)
        await update.message.reply_text("❌ Sesi upload habis. Silakan mulai ulang dengan menekan tombol 'Upload Gym Baru'.")
        return ConversationHandler.END
    
    upload_temp[user_id]["nama"] = update.message.text
    
    # Hapus pesan nama yang dikirim user
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
    except:
        pass
    
    # Hapus semua pesan sebelumnya di session
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if session.get("message_ids"):
            for msg_id in session["message_ids"]:
                await delete_message_safe(context, chat_id, msg_id)
            session["message_ids"] = []
        if session.get("message_id"):
            await delete_message_safe(context, chat_id, session["message_id"])
    
    lokasi_set = sorted({g["lokasi"] for g in GYM_DATA if g.get("lokasi")})
    if not lokasi_set:
        lokasi_set = ["Klojen", "Blimbing", "Lowokwaru"]
    buttons = [[InlineKeyboardButton(loc, callback_data=f"uploc_{loc}")] for loc in lokasi_set]
    buttons.append([InlineKeyboardButton("➕ Lainnya", callback_data="uploc_manual")])
    buttons.append([InlineKeyboardButton("❌ Batal", callback_data="cancel_upload")])
    reply = InlineKeyboardMarkup(buttons)
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="📍 Pilih *lokasi* gym dari tombol di bawah, atau pilih 'Lainnya' untuk mengetik manual:",
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)
    return UPLOAD_LOCATION

async def upload_location_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await safe_answer(query)
    
    # Cek apakah session masih aktif
    if user_id not in upload_temp:
        await reset_upload_session(user_id)
        if query and query.message:
            await delete_message_safe(context, chat_id, query.message.message_id)
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Sesi upload habis. Silakan mulai ulang dengan menekan tombol 'Upload Gym Baru'."
        )
        await save_message_id(user_id, msg.message_id)
        return ConversationHandler.END

    # Hapus pesan query
    if query and query.message:
        await delete_message_safe(context, chat_id, query.message.message_id)
    
    # Hapus semua pesan sebelumnya di session
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if session.get("message_ids"):
            for msg_id in session["message_ids"]:
                await delete_message_safe(context, chat_id, msg_id)
            session["message_ids"] = []
        if session.get("message_id"):
            await delete_message_safe(context, chat_id, session["message_id"])

    if data == "uploc_manual":
        keyboard = [[InlineKeyboardButton("❌ Batal", callback_data="cancel_upload")]]
        reply = InlineKeyboardMarkup(keyboard)
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="📍 Masukkan *lokasi* secara manual (contoh: Klojen):",
            reply_markup=reply,
            parse_mode="Markdown"
        )
        await save_message_id(user_id, msg.message_id)
        return UPLOAD_LOCATION
    else:
        lokasi = data.split("_", 1)[1]
        upload_temp[user_id]["lokasi"] = lokasi
        keyboard = [[InlineKeyboardButton("❌ Batal", callback_data="cancel_upload")]]
        reply = InlineKeyboardMarkup(keyboard)
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ Lokasi: {lokasi}\n\n📞 Kirim *nomor HP/WA* (contoh: 081234567890):",
            reply_markup=reply,
            parse_mode="Markdown"
        )
        await save_message_id(user_id, msg.message_id)
        return UPLOAD_HP

async def receive_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Cek apakah session masih aktif
    if user_id not in upload_temp:
        await reset_upload_session(user_id)
        await update.message.reply_text("❌ Sesi upload habis. Silakan mulai ulang dengan menekan tombol 'Upload Gym Baru'.")
        return ConversationHandler.END
    
    upload_temp[user_id]["lokasi"] = update.message.text
    
    # Hapus pesan lokasi yang dikirim user
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
    except:
        pass
    
    # Hapus semua pesan sebelumnya di session
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if session.get("message_ids"):
            for msg_id in session["message_ids"]:
                await delete_message_safe(context, chat_id, msg_id)
            session["message_ids"] = []
        if session.get("message_id"):
            await delete_message_safe(context, chat_id, session["message_id"])
    
    keyboard = [[InlineKeyboardButton("❌ Batal", callback_data="cancel_upload")]]
    reply = InlineKeyboardMarkup(keyboard)
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="📞 Kirim *nomor HP/WA* (contoh: 081234567890):",
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)
    return UPLOAD_HP

async def upload_hp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Cek apakah session masih aktif
    if user_id not in upload_temp:
        await reset_upload_session(user_id)
        await update.message.reply_text("❌ Sesi upload habis. Silakan mulai ulang dengan menekan tombol 'Upload Gym Baru'.")
        return ConversationHandler.END
    
    upload_temp[user_id]["hp_wa"] = update.message.text
    
    # Hapus pesan HP yang dikirim user
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
    except:
        pass
    
    # Hapus semua pesan sebelumnya di session
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if session.get("message_ids"):
            for msg_id in session["message_ids"]:
                await delete_message_safe(context, chat_id, msg_id)
            session["message_ids"] = []
        if session.get("message_id"):
            await delete_message_safe(context, chat_id, session["message_id"])
    
    keyboard = [[InlineKeyboardButton("❌ Batal", callback_data="cancel_upload")]]
    reply = InlineKeyboardMarkup(keyboard)
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="🌐 Kirim *medsos* (contoh: @gymcenter atau link):",
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)
    return UPLOAD_MEDSOS

async def upload_medsos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Cek apakah session masih aktif
    if user_id not in upload_temp:
        await reset_upload_session(user_id)
        await update.message.reply_text("❌ Sesi upload habis. Silakan mulai ulang dengan menekan tombol 'Upload Gym Baru'.")
        return ConversationHandler.END
    
    upload_temp[user_id]["medsos"] = update.message.text
    
    # Hapus pesan medsos yang dikirim user
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
    except:
        pass
    
    # Hapus semua pesan sebelumnya di session
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if session.get("message_ids"):
            for msg_id in session["message_ids"]:
                await delete_message_safe(context, chat_id, msg_id)
            session["message_ids"] = []
        if session.get("message_id"):
            await delete_message_safe(context, chat_id, session["message_id"])
    
    keyboard = [[InlineKeyboardButton("❌ Batal", callback_data="cancel_upload")]]
    reply = InlineKeyboardMarkup(keyboard)
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="🗺️ Kirim *link Google Maps* - *WAJIB*\n\n"
             "Kirim URL Google Maps yang valid, contoh:\n"
             "`https://www.google.com/maps/place/...`\n\n"
             "⚠️ *Link harus diawali dengan http:// atau https://*",
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)
    return UPLOAD_MAPS

async def upload_maps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Cek apakah session masih aktif
    if user_id not in upload_temp:
        await reset_upload_session(user_id)
        await update.message.reply_text("❌ Sesi upload habis. Silakan mulai ulang dengan menekan tombol 'Upload Gym Baru'.")
        return ConversationHandler.END
    
    # Ambil teks yang dikirim user
    text = update.message.text.strip()
    
    # Validasi apakah teks adalah URL Google Maps
    if not text.startswith(('http://', 'https://')):
        # Hapus pesan error sebelumnya jika ada
        await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
        
        # Hapus semua pesan sebelumnya di session
        if user_id in user_sessions:
            session = user_sessions[user_id]
            if session.get("message_ids"):
                for msg_id in session["message_ids"]:
                    await delete_message_safe(context, chat_id, msg_id)
                session["message_ids"] = []
            if session.get("message_id"):
                await delete_message_safe(context, chat_id, session["message_id"])
        
        # Kirim pesan error
        keyboard = [[InlineKeyboardButton("❌ Batal", callback_data="cancel_upload")]]
        reply = InlineKeyboardMarkup(keyboard)
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Mohon maaf, Anda harus menyertakan URL Google Maps yang valid.\n\n"
                 "Contoh URL Google Maps yang valid:\n"
                 "`https://www.google.com/maps/place/...`\n\n"
                 "Silakan kirim ulang link Google Maps yang benar.",
            reply_markup=reply,
            parse_mode="Markdown"
        )
        await save_message_id(user_id, msg.message_id)
        return UPLOAD_MAPS
    
    # Validasi apakah mengandung kata kunci Google Maps
    maps_keywords = ['maps.google', 'google.com/maps', 'goo.gl/maps', 'maps.app.goo.gl', 'google.com/maps/place']
    is_valid_maps = any(keyword in text.lower() for keyword in maps_keywords)
    
    if not is_valid_maps:
        # Hapus pesan error sebelumnya jika ada
        await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
        
        # Hapus semua pesan sebelumnya di session
        if user_id in user_sessions:
            session = user_sessions[user_id]
            if session.get("message_ids"):
                for msg_id in session["message_ids"]:
                    await delete_message_safe(context, chat_id, msg_id)
                session["message_ids"] = []
            if session.get("message_id"):
                await delete_message_safe(context, chat_id, session["message_id"])
        
        # Kirim pesan error
        keyboard = [[InlineKeyboardButton("❌ Batal", callback_data="cancel_upload")]]
        reply = InlineKeyboardMarkup(keyboard)
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Mohon maaf, link yang Anda kirim bukan link Google Maps yang valid.\n\n"
                 "Pastikan Anda mengirim link dari Google Maps, contoh:\n"
                 "`https://www.google.com/maps/place/...`\n\n"
                 "Silakan kirim ulang link Google Maps yang benar.",
            reply_markup=reply,
            parse_mode="Markdown"
        )
        await save_message_id(user_id, msg.message_id)
        return UPLOAD_MAPS
    
    # Simpan link maps
    upload_temp[user_id]["link_maps"] = text
    
    # Hapus pesan maps yang dikirim user
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
    except:
        pass
    
    # Hapus semua pesan sebelumnya di session
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if session.get("message_ids"):
            for msg_id in session["message_ids"]:
                await delete_message_safe(context, chat_id, msg_id)
            session["message_ids"] = []
        if session.get("message_id"):
            await delete_message_safe(context, chat_id, session["message_id"])
    
    keyboard = [
        [InlineKeyboardButton("⏭️ Skip", callback_data="skip_drive")],
        [InlineKeyboardButton("❌ Batal", callback_data="cancel_upload")]
    ]
    reply = InlineKeyboardMarkup(keyboard)
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="✅ Link Google Maps berhasil disimpan.\n\n"
             "📁 Kirim *link Drive* (foto tambahan) - *Opsional*\n\n"
             "Kirim link Drive atau ketik *skip* untuk melewati:",
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)
    return UPLOAD_DRIVE

async def upload_drive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle input link Drive atau skip"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Cek apakah session masih aktif
    if user_id not in upload_temp:
        await reset_upload_session(user_id)
        await update.message.reply_text("❌ Sesi upload habis. Silakan mulai ulang dengan menekan tombol 'Upload Gym Baru'.")
        return ConversationHandler.END
    
    if update.message and update.message.text:
        text = update.message.text.strip().lower()
        
        # Hapus pesan drive yang dikirim user
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
        except:
            pass
        
        # Hapus semua pesan sebelumnya di session
        if user_id in user_sessions:
            session = user_sessions[user_id]
            if session.get("message_ids"):
                for msg_id in session["message_ids"]:
                    await delete_message_safe(context, chat_id, msg_id)
                session["message_ids"] = []
            if session.get("message_id"):
                await delete_message_safe(context, chat_id, session["message_id"])
        
        if text == "skip":
            upload_temp[user_id]["link_drive"] = ""
            keyboard = [[InlineKeyboardButton("❌ Batal", callback_data="cancel_upload")]]
            reply = InlineKeyboardMarkup(keyboard)
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text="⏭️ Link Drive dilewati.\n\n"
                     "🖼️ Kirim *foto utama* gym (kirim gambar):",
                reply_markup=reply,
                parse_mode="Markdown"
            )
            await save_message_id(user_id, msg.message_id)
            return UPLOAD_PHOTO
        else:
            # Simpan link Drive (opsional, bisa apa saja)
            upload_temp[user_id]["link_drive"] = update.message.text
            keyboard = [[InlineKeyboardButton("❌ Batal", callback_data="cancel_upload")]]
            reply = InlineKeyboardMarkup(keyboard)
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text="✅ Link Drive disimpan.\n\n"
                     "🖼️ Kirim *foto utama* gym (kirim gambar):",
                reply_markup=reply,
                parse_mode="Markdown"
            )
            await save_message_id(user_id, msg.message_id)
            return UPLOAD_PHOTO
    
    return UPLOAD_DRIVE

async def skip_drive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Skip link Drive"""
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await safe_answer(query)
    
    # Cek apakah session masih aktif
    if user_id not in upload_temp:
        await reset_upload_session(user_id)
        if query and query.message:
            await delete_message_safe(context, chat_id, query.message.message_id)
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Sesi upload habis. Silakan mulai ulang dengan menekan tombol 'Upload Gym Baru'."
        )
        await save_message_id(user_id, msg.message_id)
        return ConversationHandler.END
    
    upload_temp[user_id]["link_drive"] = ""
    
    # Hapus pesan query
    if query and query.message:
        await delete_message_safe(context, chat_id, query.message.message_id)
    
    # Hapus semua pesan sebelumnya di session
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if session.get("message_ids"):
            for msg_id in session["message_ids"]:
                await delete_message_safe(context, chat_id, msg_id)
            session["message_ids"] = []
        if session.get("message_id"):
            await delete_message_safe(context, chat_id, session["message_id"])
    
    keyboard = [[InlineKeyboardButton("❌ Batal", callback_data="cancel_upload")]]
    reply = InlineKeyboardMarkup(keyboard)
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="⏭️ Link Drive dilewati.\n\n"
             "🖼️ Kirim *foto utama* gym (kirim gambar):",
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)
    return UPLOAD_PHOTO

async def upload_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Cek apakah session masih aktif
    if user_id not in upload_temp:
        await reset_upload_session(user_id)
        await update.message.reply_text("❌ Sesi upload habis. Silakan mulai ulang dengan menekan tombol 'Upload Gym Baru'.")
        return ConversationHandler.END
    
    try:
        photo_file = update.message.photo[-1]
        
        # Hapus pesan foto yang dikirim user
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)
        except:
            pass
        
        # Hapus semua pesan sebelumnya di session
        if user_id in user_sessions:
            session = user_sessions[user_id]
            if session.get("message_ids"):
                for msg_id in session["message_ids"]:
                    await delete_message_safe(context, chat_id, msg_id)
                session["message_ids"] = []
            if session.get("message_id"):
                await delete_message_safe(context, chat_id, session["message_id"])
        
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="⏳ Sedang mengupload gambar..."
        )
        await save_message_id(user_id, msg.message_id)
        
        file = await context.bot.get_file(photo_file.file_id)
        timestamp = int(time.time())
        filename = f"upload_{user_id}_{timestamp}.jpg"
        filepath = get_image_path(filename)
        
        await file.download_to_drive(filepath)
        upload_temp[user_id]["gambar"] = filename
        
        data = upload_temp[user_id]
        
        caption_text = (
            f"📋 Konfirmasi Data Gym\n\n"
            f"📌 Nama: {data['nama']}\n"
            f"📍 Lokasi: {data['lokasi']}\n"
            f"📞 HP/WA: {data['hp_wa']}\n"
            f"🌐 Medsos: {data['medsos']}\n"
            f"🗺️ Maps: {data['link_maps']}\n"
            f"📁 Drive: {data['link_drive'] if data.get('link_drive') else 'Tidak ada'}\n\n"
            f"⚠️ Gym akan masuk verifikasi admin.\n"
            f"Apakah data sudah benar?"
        )
        
        keyboard = [
            [InlineKeyboardButton("✅ Simpan", callback_data="confirm_yes"),
             InlineKeyboardButton("❌ Batal", callback_data="confirm_no")]
        ]
        reply = InlineKeyboardMarkup(keyboard)
        
        # Hapus pesan "Sedang mengupload gambar..."
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
        except:
            pass
        
        with open(filepath, "rb") as photo:
            msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(photo),
                caption=caption_text,
                reply_markup=reply
            )
            await save_message_id(user_id, msg.message_id)
        return UPLOAD_CONFIRM
        
    except Exception as e:
        logger.error(f"Error upload photo: {e}")
        # Hapus semua pesan sebelumnya
        if user_id in user_sessions:
            session = user_sessions[user_id]
            if session.get("message_ids"):
                for msg_id in session["message_ids"]:
                    await delete_message_safe(context, chat_id, msg_id)
                session["message_ids"] = []
            if session.get("message_id"):
                await delete_message_safe(context, chat_id, session["message_id"])
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Gagal mengupload gambar. Silakan coba lagi.\n"
                 "Pastikan gambar tidak terlalu besar (maks 20MB)."
        )
        await save_message_id(user_id, msg.message_id)
        return UPLOAD_PHOTO

async def confirm_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await safe_answer(query)
    
    data = upload_temp.get(user_id)
    if not data:
        await reset_upload_session(user_id)
        if query and query.message:
            await delete_message_safe(context, chat_id, query.message.message_id)
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ Data tidak ditemukan. Silakan mulai ulang dengan menekan tombol 'Upload Gym Baru'."
        )
        await save_message_id(user_id, msg.message_id)
        return ConversationHandler.END

    # Hapus pesan konfirmasi
    if query and query.message:
        await delete_message_safe(context, chat_id, query.message.message_id)
    
    # Hapus semua pesan sebelumnya di session
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if session.get("message_ids"):
            for msg_id in session["message_ids"]:
                await delete_message_safe(context, chat_id, msg_id)
            session["message_ids"] = []
        if session.get("message_id"):
            await delete_message_safe(context, chat_id, session["message_id"])

    if query.data == "confirm_yes":
        new_id = max([g["id"] for g in GYM_DATA], default=0) + 1
        gym_baru = {
            "id": new_id,
            "nama": data["nama"],
            "lokasi": data["lokasi"],
            "hp_wa": data["hp_wa"],
            "medsos": data["medsos"],
            "link_maps": data["link_maps"],
            "link_drive": data.get("link_drive", ""),
            "gambar": data["gambar"],
            "status": "pending",
            "latitude": None,
            "longitude": None
        }
        GYM_DATA.append(gym_baru)
        save_gym_data()
        
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="✅ **Gym berhasil diupload dan menunggu verifikasi admin.**",
            parse_mode="Markdown"
        )
        await save_message_id(user_id, msg.message_id)
        
        if ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"🆕 *Gym Baru Pending*\nNama: {gym_baru['nama']}\nLokasi: {gym_baru['lokasi']}\nID: {new_id}",
                    parse_mode="Markdown"
                )
            except:
                pass
    else:
        if data.get("gambar") and os.path.exists(get_image_path(data["gambar"])):
            os.remove(get_image_path(data["gambar"]))
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Upload dibatalkan."
        )
        await save_message_id(user_id, msg.message_id)

    upload_temp.pop(user_id, None)
    
    # Tampilkan menu utama setelah beberapa detik
    await asyncio.sleep(1)
    await show_main_menu(update, context)
    return ConversationHandler.END

async def cancel_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await reset_upload_session(user_id)
    
    # Hapus semua pesan
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if session.get("message_ids"):
            for msg_id in session["message_ids"]:
                await delete_message_safe(context, chat_id, msg_id)
            session["message_ids"] = []
        if session.get("message_id"):
            await delete_message_safe(context, chat_id, session["message_id"])
        user_sessions.pop(user_id, None)
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="❌ Upload dibatalkan."
    )
    await save_message_id(user_id, msg.message_id)
    await asyncio.sleep(1)
    await show_main_menu(update, context)
    return ConversationHandler.END

async def cancel_upload_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await safe_answer(query)
    
    # Reset upload session
    await reset_upload_session(user_id)
    
    # Hapus semua pesan
    if user_id in user_sessions:
        session = user_sessions[user_id]
        if session.get("message_ids"):
            for msg_id in session["message_ids"]:
                await delete_message_safe(context, chat_id, msg_id)
            session["message_ids"] = []
        if session.get("message_id"):
            await delete_message_safe(context, chat_id, session["message_id"])
        user_sessions.pop(user_id, None)
    
    # Hapus pesan query
    if query and query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    
    await show_main_menu(update, context)
    return ConversationHandler.END

# ==================== ADMIN PANEL ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await safe_answer(update.callback_query, "Anda bukan admin.", show_alert=True)
        return
    
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Reset upload session
    await reset_upload_session(user_id)
    
    # Hapus semua pesan sebelumnya
    await clear_all_messages(context, chat_id, user_id)
    
    # Hapus pesan query
    if query and query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    
    pending = [g for g in GYM_DATA if g.get("status") == "pending"]
    
    keyboard = []
    
    if pending:
        keyboard.append([InlineKeyboardButton("📋 Gym Pending", callback_data="admin_pending")])
    
    keyboard.append([InlineKeyboardButton("📍 Tambah Koordinat", callback_data="admin_koordinat")])
    keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="main")])
    
    reply = InlineKeyboardMarkup(keyboard)
    
    status_text = f"📊 **Admin Panel**\n\n"
    status_text += f"📌 Total Gym: {len(GYM_DATA)}\n"
    status_text += f"⏳ Pending: {len(pending)}\n"
    status_text += f"✅ Approved: {len([g for g in GYM_DATA if g.get('status') == 'approved'])}\n"
    
    gyms_without_coord = [g for g in GYM_DATA if g.get("status") == "approved" and (not g.get("latitude") or not g.get("longitude"))]
    status_text += f"📍 Tanpa Koordinat: {len(gyms_without_coord)}"
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=status_text,
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)

async def admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan daftar gym pending"""
    if not is_admin(update.effective_user.id):
        return
    
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Hapus semua pesan sebelumnya
    await clear_all_messages(context, chat_id, user_id)
    
    # Hapus pesan query
    if query and query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    
    pending = [g for g in GYM_DATA if g.get("status") == "pending"]
    
    if not pending:
        keyboard = [[InlineKeyboardButton("🔙 Kembali", callback_data="admin_panel")]]
        reply = InlineKeyboardMarkup(keyboard)
        await safe_answer(query, "Tidak ada gym pending.")
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="✅ Tidak ada gym pending.",
            reply_markup=reply
        )
        await save_message_id(user_id, msg.message_id)
        return
    
    keyboard = []
    for gym in pending:
        label = f"⏳ {gym['nama']} - {gym['lokasi']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"admin_detail_{gym['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="admin_panel")])
    reply = InlineKeyboardMarkup(keyboard)
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="📋 **Daftar Gym Pending**",
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)

async def admin_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Hapus semua pesan sebelumnya
    await clear_all_messages(context, chat_id, user_id)
    
    # Hapus pesan query
    if query and query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    
    gym_id = int(query.data.split("_")[2])
    gym = next((g for g in GYM_DATA if g["id"] == gym_id), None)
    if not gym:
        await safe_answer(query, "Tidak ditemukan.", show_alert=True)
        return
    
    caption_text = (
        f"📌 Nama: {gym['nama']}\n"
        f"📍 Lokasi: {gym['lokasi']}\n"
        f"📞 HP/WA: {gym['hp_wa']}\n"
        f"🌐 Medsos: {gym['medsos']}\n"
        f"🗺️ Maps: {gym['link_maps']}\n"
        f"📁 Drive: {gym['link_drive']}\n"
        f"🆔 ID: {gym['id']}"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ Setujui", callback_data=f"approve_{gym_id}"),
         InlineKeyboardButton("❌ Tolak", callback_data=f"reject_{gym_id}")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="admin_pending")]
    ]
    reply = InlineKeyboardMarkup(keyboard)
    
    image_path = get_image_path(gym.get("gambar", ""))
    try:
        if os.path.exists(image_path) and os.path.getsize(image_path) > 0:
            with open(image_path, "rb") as photo:
                msg = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=InputFile(photo),
                    caption=caption_text,
                    reply_markup=reply
                )
                await save_message_id(user_id, msg.message_id)
        else:
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=f"{caption_text}\n\n⚠️ Gambar tidak tersedia",
                reply_markup=reply
            )
            await save_message_id(user_id, msg.message_id)
    except Exception as e:
        logger.error(f"Error admin detail: {e}")
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"{caption_text}\n\n⚠️ Error menampilkan gambar",
            reply_markup=reply
        )
        await save_message_id(user_id, msg.message_id)

async def approve_gym(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    gym_id = int(query.data.split("_")[1])
    gym = next((g for g in GYM_DATA if g["id"] == gym_id), None)
    if gym:
        gym["status"] = "approved"
        save_gym_data()
        await safe_answer(query, "✅ Gym disetujui!")
    
    # Hapus semua pesan
    await clear_all_messages(context, chat_id, user_id)
    await admin_pending(update, context)

async def reject_gym(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    gym_id = int(query.data.split("_")[1])
    gym = next((g for g in GYM_DATA if g["id"] == gym_id), None)
    if gym:
        GYM_DATA.remove(gym)
        save_gym_data()
        image_path = get_image_path(gym.get("gambar", ""))
        if os.path.exists(image_path):
            try:
                os.remove(image_path)
            except:
                pass
        await safe_answer(query, "❌ Gym ditolak dan dihapus.")
    
    # Hapus semua pesan
    await clear_all_messages(context, chat_id, user_id)
    await admin_pending(update, context)

# ==================== ADMIN TAMBAH KOORDINAT ====================
async def admin_koordinat_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan daftar gym yang belum punya koordinat"""
    if not is_admin(update.effective_user.id):
        await safe_answer(update.callback_query, "Anda bukan admin.", show_alert=True)
        return
    
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Hapus semua pesan sebelumnya
    await clear_all_messages(context, chat_id, user_id)
    
    # Hapus pesan query
    if query and query.message:
        await delete_message_safe(context, query.message.chat_id, query.message.message_id)
    
    gyms_without_coord = [g for g in GYM_DATA if g.get("status") == "approved" and (not g.get("latitude") or not g.get("longitude"))]
    
    if not gyms_without_coord:
        keyboard = [[InlineKeyboardButton("🔙 Kembali", callback_data="admin_panel")]]
        reply = InlineKeyboardMarkup(keyboard)
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="✅ Semua gym sudah memiliki koordinat!",
            reply_markup=reply
        )
        await save_message_id(user_id, msg.message_id)
        return
    
    context.user_data['coord_mode'] = 'waiting'
    
    keyboard = []
    for gym in gyms_without_coord:
        label = f"📍 {gym['nama']} - {gym['lokasi']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"coord_{gym['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="admin_panel")])
    reply = InlineKeyboardMarkup(keyboard)
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="📋 **Daftar Gym yang Belum Punya Koordinat**\n\n"
             "Pilih gym untuk menambahkan koordinat:",
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)

async def admin_add_coord(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan instruksi untuk menambahkan koordinat"""
    if not is_admin(update.effective_user.id):
        return
    
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Hapus semua pesan sebelumnya
    await clear_all_messages(context, chat_id, user_id)
    
    gym_id = int(query.data.split("_")[1])
    gym = next((g for g in GYM_DATA if g["id"] == gym_id), None)
    
    if not gym:
        await safe_answer(query, "Gym tidak ditemukan.", show_alert=True)
        return
    
    context.user_data['coord_gym_id'] = gym_id
    context.user_data['coord_mode'] = 'waiting'
    
    keyboard = [[InlineKeyboardButton("🔙 Batal", callback_data="admin_koordinat")]]
    reply = InlineKeyboardMarkup(keyboard)
    
    text = (
        f"📍 **Tambahkan Koordinat untuk {gym['nama']}**\n\n"
        f"Lokasi: {gym['lokasi']}\n\n"
        f"Cara mendapatkan koordinat:\n"
        f"1. Buka Google Maps di browser/HP\n"
        f"2. Cari lokasi gym\n"
        f"3. Klik kanan pada lokasi (atau tap tahan di HP)\n"
        f"4. Pilih 'Koordinat' atau 'What's here?'\n"
        f"5. Salin koordinat yang muncul\n\n"
        f"Kirim koordinat dengan format:\n"
        f"`latitude,longitude`\n"
        f"Contoh: `-7.981894,112.626506`\n\n"
        f"*Kirim koordinat di chat ini*"
    )
    
    msg = await query.edit_message_text(
        text=text,
        reply_markup=reply,
        parse_mode="Markdown"
    )
    await save_message_id(user_id, msg.message_id)
    await safe_answer(query)

async def handle_coord_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle teks koordinat yang dikirim admin"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ Anda bukan admin.")
        return
    
    if context.user_data.get('coord_mode') != 'waiting':
        return
    
    gym_id = context.user_data.get('coord_gym_id')
    if not gym_id:
        await update.message.reply_text("❌ Sesi habis. Silakan mulai dari admin panel.")
        context.user_data['coord_mode'] = None
        return
    
    gym = next((g for g in GYM_DATA if g["id"] == gym_id), None)
    if not gym:
        await update.message.reply_text("❌ Gym tidak ditemukan.")
        context.user_data['coord_mode'] = None
        return
    
    try:
        coord_text = update.message.text.strip().replace(" ", "")
        parts = coord_text.split(",")
        
        if len(parts) != 2:
            await update.message.reply_text(
                "❌ Format salah!\n\n"
                "Kirim koordinat dengan format:\n"
                "`latitude,longitude`\n"
                "Contoh: `-7.981894,112.626506`",
                parse_mode="Markdown"
            )
            return
        
        latitude = float(parts[0])
        longitude = float(parts[1])
        
        if not (-90 <= latitude <= 90):
            await update.message.reply_text("❌ Latitude harus antara -90 dan 90")
            return
        if not (-180 <= longitude <= 180):
            await update.message.reply_text("❌ Longitude harus antara -180 dan 180")
            return
        
        gym["latitude"] = latitude
        gym["longitude"] = longitude
        save_gym_data()
        
        await update.message.reply_text(
            f"✅ **Koordinat berhasil ditambahkan!**\n\n"
            f"📍 Gym: {gym['nama']}\n"
            f"📍 Lokasi: {gym['lokasi']}\n"
            f"🌐 Koordinat: {latitude}, {longitude}\n\n"
            f"Sekarang gym ini bisa ditemukan melalui fitur 'Gym Terdekat'!"
        )
        
        context.user_data['coord_mode'] = None
        context.user_data.pop('coord_gym_id', None)
        
        await admin_koordinat_list(update, context)
        
    except ValueError:
        await update.message.reply_text(
            "❌ Format angka salah! Gunakan titik (.) sebagai desimal.\n"
            "Contoh: `-7.981894,112.626506`",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error in handle_coord_text: {e}")
        await update.message.reply_text(f"❌ Terjadi kesalahan: {e}")

async def coord_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Membatalkan penambahan koordinat"""
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await safe_answer(query, "Dibatalkan.")
    context.user_data.pop('coord_gym_id', None)
    context.user_data['coord_mode'] = None
    
    # Hapus semua pesan
    await clear_all_messages(context, chat_id, user_id)
    await admin_panel(update, context)

# ==================== ROUTER ====================
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await safe_answer(query)

    if data == "main":
        await show_main_menu(update, context)
    elif data == "menu_cari":
        await menu_cari(update, context)
    elif data == "menu_lokasi":
        await menu_lokasi(update, context)
    elif data == "kirim_lokasi":
        await kirim_lokasi_prompt(update, context)
    elif data == "cari_dengan_lokasi_tersimpan":
        await cari_dengan_lokasi_tersimpan(update, context)
    elif data == "lihat_gym_terdekat":
        await lihat_gym_terdekat(update, context)
    elif data == "menu_upload":
        pass
    elif data == "skip_drive":
        await skip_drive(update, context)
    elif data == "admin_panel":
        await admin_panel(update, context)
    elif data == "admin_pending":
        await admin_pending(update, context)
    elif data == "admin_koordinat":
        await admin_koordinat_list(update, context)
    elif data.startswith("coord_"):
        await admin_add_coord(update, context)
    elif data == "coord_cancel":
        await coord_cancel(update, context)
    elif data == "back_to_gym":
        await back_to_gym_after_rating(update, context)
    elif data == "rekomendasi":
        await rekomendasi_gym(update, context)
    elif data == "cari_wilayah":
        await cari_wilayah(update, context)
    elif data.startswith("wilayah_"):
        await pilih_wilayah(update, context)
    elif data.startswith("nav_"):
        await nav_slide(update, context)
    elif data == "back_to_list":
        await back_to_list(update, context)
    elif data.startswith("admin_detail_"):
        await admin_detail(update, context)
    elif data.startswith("approve_"):
        await approve_gym(update, context)
    elif data.startswith("reject_"):
        await reject_gym(update, context)
    elif data.startswith("show_rating_"):
        await show_rating_menu(update, context)
    elif data.startswith("give_rating_"):
        await proses_rating(update, context)
    elif data == "cancel_rating":
        await cancel_rating(update, context)
    elif data.startswith("uploc_"):
        pass
    elif data == "cancel_upload":
        await cancel_upload_callback(update, context)
        return
    else:
        await show_main_menu(update, context)

# ==================== FUNGSI LAINNYA ====================
async def lihat_gym_terdekat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menampilkan detail gym terdekat"""
    query = update.callback_query
    user_id = update.effective_user.id
    session = user_sessions.get(user_id)
    
    if not session or "list" not in session:
        await safe_answer(query, "Sesi habis. Silakan cari lagi.", show_alert=True)
        return
    
    session["index"] = 0
    await tampilkan_gym(update, context, user_id)

# ==================== MAIN ====================
def main():
    app = (
        Application.builder()
        .token(TOKEN)
        .connect_timeout(60.0)
        .read_timeout(60.0)
        .write_timeout(60.0)
        .build()
    )

    upload_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_upload, pattern="^menu_upload$")],
        states={
            UPLOAD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, upload_name)],
            UPLOAD_LOCATION: [
                CallbackQueryHandler(upload_location_callback, pattern="^uploc_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_location)
            ],
            UPLOAD_HP: [MessageHandler(filters.TEXT & ~filters.COMMAND, upload_hp)],
            UPLOAD_MEDSOS: [MessageHandler(filters.TEXT & ~filters.COMMAND, upload_medsos)],
            UPLOAD_MAPS: [MessageHandler(filters.TEXT & ~filters.COMMAND, upload_maps)],
            UPLOAD_DRIVE: [
                CallbackQueryHandler(skip_drive, pattern="^skip_drive$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, upload_drive)
            ],
            UPLOAD_PHOTO: [MessageHandler(filters.PHOTO, upload_photo)],
            UPLOAD_CONFIRM: [CallbackQueryHandler(confirm_upload, pattern="^confirm_")]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_upload),
            CallbackQueryHandler(cancel_upload_callback, pattern="^cancel_upload$")
        ],
        per_message=False,
        per_chat=True
    )

    app.add_handler(CommandHandler("start", welcome_page))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(upload_conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_coord_text))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: None))

    logger.info("🤖 Gym Bot berjalan dengan fitur lokasi terdekat...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()