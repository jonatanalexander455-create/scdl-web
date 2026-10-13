import os
import sys
import time
import shutil
import logging
from datetime import datetime
import requests

try:
    from db import (
        get_setting,
        set_setting,
        add_user,
        extend_user,
        delete_user,
        reset_user_password,
        get_all_users,
        get_recent_downloads,
        get_user_by_username,
    )
except ImportError:
    from web.db import (
        get_setting,
        set_setting,
        add_user,
        extend_user,
        delete_user,
        reset_user_password,
        get_all_users,
        get_recent_downloads,
        get_user_by_username,
    )

logger = logging.getLogger("telegram_bot")


def get_telegram_config():
    """Retrieve Telegram bot token and admin chat ID from DB or environment."""
    token = get_setting('telegram_bot_token', '').strip()
    if not token:
        token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()

    admin_id = get_setting('telegram_admin_id', '').strip()
    if not admin_id:
        admin_id = os.environ.get('TELEGRAM_ADMIN_CHAT_ID', '').strip()

    return {
        'token': token,
        'admin_id': admin_id,
        'is_configured': bool(token)
    }


def set_telegram_config(token, admin_id):
    """Save Telegram configuration into settings table."""
    set_setting('telegram_bot_token', str(token or '').strip())
    set_setting('telegram_admin_id', str(admin_id or '').strip())


def send_telegram_message(token, chat_id, text, reply_markup=None, parse_mode='HTML'):
    """Send a message to a Telegram chat using Telegram Bot API."""
    if not token or not chat_id:
        return False, "Token atau Chat ID tidak tersedia"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': parse_mode,
        'disable_web_page_preview': True
    }
    if reply_markup:
        payload['reply_markup'] = reply_markup
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if data.get('ok'):
            return True, data
        return False, data.get('description', 'Telegram API error')
    except Exception as e:
        logger.error(f"[Telegram] Error sending message: {e}")
        return False, str(e)


def answer_callback_query(token, callback_query_id, text=None):
    """Acknowledge a callback query to stop loading spinner in Telegram client."""
    if not token or not callback_query_id:
        return
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    payload = {'callback_query_id': callback_query_id}
    if text:
        payload['text'] = text
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"[Telegram] Error answering callback query: {e}")


def register_webhook(token, webhook_url):
    """Register webhook with Telegram API."""
    if not token:
        return False, "Bot token kosong"
    url = f"https://api.telegram.org/bot{token}/setWebhook"
    try:
        resp = requests.post(url, json={
            'url': webhook_url,
            'drop_pending_updates': True,
            'allowed_updates': ['message', 'callback_query']
        }, timeout=10)
        data = resp.json()
        if data.get('ok'):
            return True, data.get('description', 'Webhook berhasil didaftarkan!')
        return False, data.get('description', 'Gagal mendaftarkan webhook')
    except Exception as e:
        return False, str(e)


def delete_webhook(token):
    """Remove webhook from Telegram."""
    if not token:
        return False, "Bot token kosong"
    url = f"https://api.telegram.org/bot{token}/deleteWebhook"
    try:
        resp = requests.post(url, json={'drop_pending_updates': True}, timeout=10)
        data = resp.json()
        return data.get('ok', False), data.get('description', '')
    except Exception as e:
        return False, str(e)


def build_menu_keyboard():
    """Build the main inline keyboard dashboard."""
    return {
        'inline_keyboard': [
            [
                {'text': '👥 List Member', 'callback_data': 'cmd:users'},
                {'text': '📊 Status Server', 'callback_data': 'cmd:status'}
            ],
            [
                {'text': '📜 Log Download', 'callback_data': 'cmd:log'},
                {'text': '🔑 Master Token', 'callback_data': 'cmd:token'}
            ],
            [
                {'text': '➕ Tambah Member', 'callback_data': 'cmd:add_help'},
                {'text': '❓ Panduan Perintah', 'callback_data': 'cmd:help'}
            ]
        ]
    }


def format_wa_text(username, password, days, expires_str):
    """Format message template ready to copy and send to WhatsApp."""
    return (
        f"Halo kak! Akun SoundCloud Downloader Premium sudah aktif ya ✨\n\n"
        f"🔗 Link Login: https://soundcloud.my.id/login\n"
        f"👤 Username: {username}\n"
        f"🔑 Password: {password}\n"
        f"⏳ Durasi: {days} Hari (Berlaku s/d: {expires_str})\n"
        f"📱 Batas Login: 1 Perangkat Aktif\n\n"
        f"Silakan login dan nikmati download lagu SoundCloud kualitas jernih tanpa batas. Terima kasih! 🙏"
    )


def handle_telegram_update(update):
    """
    Main webhook entry point. Parses Telegram update, verifies authorization,
    and dispatches commands and callback queries.
    """
    config = get_telegram_config()
    token = config['token']
    admin_id = config['admin_id']

    if not token:
        logger.warning("[Telegram] Update received but bot token not configured.")
        return {'status': 'ignored', 'reason': 'no_token'}

    # 1. Handle Callback Query
    if 'callback_query' in update:
        cb = update['callback_query']
        cb_id = cb.get('id')
        from_user = cb.get('from', {})
        sender_id = str(from_user.get('id', ''))
        data = cb.get('data', '')
        chat_id = cb.get('message', {}).get('chat', {}).get('id')

        # Check authorization
        if not admin_id or sender_id != str(admin_id):
            answer_callback_query(token, cb_id, "⛔ Akses ditolak! Anda bukan Admin.")
            send_telegram_message(
                token, chat_id,
                f"⛔ <b>Akses Ditolak!</b>\nChat ID Anda: <code>{sender_id}</code>\nAkun Anda belum terdaftar sebagai Admin di SoundCloud Web."
            )
            return {'status': 'unauthorized'}

        answer_callback_query(token, cb_id)
        return handle_callback_action(token, chat_id, data)

    # 2. Handle Message
    if 'message' in update:
        msg = update['message']
        chat = msg.get('chat', {})
        chat_id = chat.get('id')
        sender_id = str(msg.get('from', {}).get('id', chat_id))
        text = (msg.get('text') or '').strip()

        if not text:
            return {'status': 'ignored', 'reason': 'empty_text'}

        # Check authorization
        if not admin_id:
            # First-time setup helper message
            send_telegram_message(
                token, chat_id,
                f"👋 <b>Halo! Bot Admin SoundCloud Siap Digunakan.</b>\n\n"
                f"Telegram Chat ID Anda adalah: <code>{sender_id}</code>\n\n"
                f"⚠️ <i>Admin Chat ID belum dikonfigurasi di website.</i>\n"
                f"Salin ID di atas dan masukkan ke menu <b>Kelola SCDL</b> di website https://soundcloud.my.id/kelola-scdl untuk menghubungkan akun Telegram Anda secara penuh."
            )
            return {'status': 'pending_admin_setup'}

        if sender_id != str(admin_id):
            send_telegram_message(
                token, chat_id,
                f"⛔ <b>Akses Ditolak!</b>\n\n"
                f"Chat ID Anda: <code>{sender_id}</code>\n"
                f"Anda tidak memiliki izin untuk mengontrol sistem SoundCloud Downloader."
            )
            return {'status': 'unauthorized'}

        # Execute command
        return handle_command(token, chat_id, text)

    return {'status': 'ignored'}


def handle_callback_action(token, chat_id, action):
    """Execute action triggered by inline keyboard buttons."""
    if action == 'cmd:menu':
        return send_menu(token, chat_id)
    elif action == 'cmd:users':
        return cmd_users(token, chat_id)
    elif action == 'cmd:status':
        return cmd_status(token, chat_id)
    elif action == 'cmd:log':
        return cmd_log(token, chat_id)
    elif action == 'cmd:token':
        return cmd_token(token, chat_id, None)
    elif action == 'cmd:add_help':
        help_text = (
            "➕ <b>Format Tambah Member Baru:</b>\n\n"
            "Ketik perintah:\n"
            "<code>/tambah [username] [password] [hari]</code>\n\n"
            "Contoh:\n"
            "<code>/tambah andi rahasia123 30</code>\n\n"
            "💡 <i>Sistem akan otomatis membuat akun dan menyiapkan pesan WhatsApp siap kirim ke customer!</i>"
        )
        send_telegram_message(token, chat_id, help_text, reply_markup=build_menu_keyboard())
        return {'status': 'ok'}
    elif action == 'cmd:help':
        return cmd_help(token, chat_id)
    return {'status': 'unknown_action'}


def handle_command(token, chat_id, full_text):
    """Parse and route text commands."""
    parts = full_text.split()
    cmd = parts[0].lower() if parts else ''
    args = parts[1:]

    # Remove bot username mention e.g. /start@MyBot -> /start
    if '@' in cmd:
        cmd = cmd.split('@')[0]

    if cmd in ['/start', '/menu', 'menu', 'start']:
        return send_menu(token, chat_id)

    elif cmd in ['/users', '/member', '/members', 'users', 'member']:
        return cmd_users(token, chat_id)

    elif cmd in ['/tambah', '/add', '/create']:
        return cmd_tambah(token, chat_id, args)

    elif cmd in ['/perpanjang', '/extend']:
        return cmd_perpanjang(token, chat_id, args)

    elif cmd in ['/resetpass', '/reset']:
        return cmd_resetpass(token, chat_id, args)

    elif cmd in ['/hapus', '/delete', '/del']:
        return cmd_hapus(token, chat_id, args)

    elif cmd in ['/status', '/info', 'status']:
        return cmd_status(token, chat_id)

    elif cmd in ['/log', '/logs', 'log']:
        return cmd_log(token, chat_id)

    elif cmd in ['/token']:
        new_token = args[0] if args else None
        return cmd_token(token, chat_id, new_token)

    elif cmd in ['/bantuan', '/help', 'help']:
        return cmd_help(token, chat_id)

    else:
        send_telegram_message(
            token, chat_id,
            f"❓ Perintah <code>{cmd}</code> tidak dikenali.\nKetik /menu atau /bantuan untuk melihat daftar perintah yang tersedia.",
            reply_markup=build_menu_keyboard()
        )
        return {'status': 'unknown_command'}


def send_menu(token, chat_id):
    """Send welcome dashboard with statistics overview."""
    users = get_all_users()
    active_count = sum(1 for u in users if u.get('is_active'))
    expired_count = len(users) - active_count

    now_str = datetime.now().strftime('%d-%m-%Y %H:%M WIB')

    text = (
        f"⚡ <b>SOUNDCLOUD WEB ADMIN TELEGRAM</b> ⚡\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 <b>Waktu Server:</b> {now_str}\n"
        f"👥 <b>Total Member:</b> {len(users)} Akun\n"
        f"🟢 <b>Member Aktif:</b> {active_count}\n"
        f"🔴 <b>Member Expired:</b> {expired_count}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Pilih menu di bawah atau ketik perintah langsung:"
    )
    send_telegram_message(token, chat_id, text, reply_markup=build_menu_keyboard())
    return {'status': 'ok'}


def cmd_users(token, chat_id):
    """List all registered members with real-time status."""
    users = get_all_users()
    if not users:
        send_telegram_message(token, chat_id, "ℹ️ Belum ada member yang terdaftar di database.", reply_markup=build_menu_keyboard())
        return {'status': 'ok'}

    lines = ["👥 <b>DAFTAR MEMBER SOUNDCLOUD:</b>\n━━━━━━━━━━━━━━━━━━━━"]
    for idx, u in enumerate(users, 1):
        uname = u.get('username')
        is_active = u.get('is_active')
        days = u.get('days_left', 0)
        exp = str(u.get('expires_at', ''))[:10]

        if is_active:
            status_badge = f"🟢 <b>{uname}</b> — Sisa {days} hari (s/d {exp})"
        else:
            status_badge = f"🔴 <b>{uname}</b> — <i>EXPIRED</i> (s/d {exp})"
        lines.append(f"{idx}. {status_badge}")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"Total: {len(users)} Member | Aktif: {sum(1 for u in users if u.get('is_active'))}")

    # Split long messages if exceeding Telegram limit (4096 chars)
    full_text = "\n".join(lines)
    if len(full_text) > 4000:
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 1 > 4000:
                send_telegram_message(token, chat_id, chunk)
                chunk = line + "\n"
            else:
                chunk += line + "\n"
        if chunk:
            send_telegram_message(token, chat_id, chunk, reply_markup=build_menu_keyboard())
    else:
        send_telegram_message(token, chat_id, full_text, reply_markup=build_menu_keyboard())
    return {'status': 'ok'}


def cmd_tambah(token, chat_id, args):
    """Add a new member and return a ready-to-copy WhatsApp message."""
    if len(args) < 3:
        usage = (
            "⚠️ <b>Format Salah!</b>\n\n"
            "Gunakan format:\n"
            "<code>/tambah [username] [password] [hari]</code>\n\n"
            "Contoh:\n"
            "<code>/tambah bayu rahasia123 30</code>"
        )
        send_telegram_message(token, chat_id, usage)
        return {'status': 'bad_args'}

    username = args[0].strip()
    password = args[1].strip()
    try:
        days = int(args[2].strip())
        if days <= 0:
            raise ValueError()
    except ValueError:
        send_telegram_message(token, chat_id, "⚠️ Durasi hari harus berupa angka bulat positif (misal: 30).")
        return {'status': 'bad_days'}

    ok, msg = add_user(username, password, is_admin=False, days=days)
    if not ok:
        send_telegram_message(token, chat_id, f"❌ <b>Gagal menambahkan user:</b>\n{msg}")
        return {'status': 'error', 'message': msg}

    # Fetch created user for precise expiry date
    user_info = get_user_by_username(username)
    exp_str = str(user_info.get('expires_at', ''))[:10] if user_info else '-'

    wa_text = format_wa_text(username, password, days, exp_str)

    reply = (
        f"✅ <b>BERHASIL MENAMBAHKAN MEMBER!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Username:</b> <code>{username}</code>\n"
        f"🔑 <b>Password:</b> <code>{password}</code>\n"
        f"⏳ <b>Durasi:</b> {days} Hari (s/d {exp_str})\n\n"
        f"📋 <b>Format Siap Kirim ke WhatsApp (Ketuk untuk Salin):</b>\n"
        f"<code>{wa_text}</code>"
    )
    send_telegram_message(token, chat_id, reply, reply_markup=build_menu_keyboard())
    return {'status': 'ok'}


def cmd_perpanjang(token, chat_id, args):
    """Extend user subscription and generate ready-to-copy WhatsApp message."""
    if len(args) < 2:
        usage = (
            "⚠️ <b>Format Salah!</b>\n\n"
            "Gunakan format:\n"
            "<code>/perpanjang [username] [hari]</code>\n\n"
            "Contoh:\n"
            "<code>/perpanjang bayu 30</code>"
        )
        send_telegram_message(token, chat_id, usage)
        return {'status': 'bad_args'}

    username = args[0].strip()
    try:
        days = int(args[1].strip())
        if days <= 0:
            raise ValueError()
    except ValueError:
        send_telegram_message(token, chat_id, "⚠️ Durasi hari harus berupa angka bulat positif (misal: 30).")
        return {'status': 'bad_days'}

    user = get_user_by_username(username)
    if not user:
        send_telegram_message(token, chat_id, f"❌ User dengan username <b>{username}</b> tidak ditemukan!")
        return {'status': 'not_found'}

    if user.get('is_admin'):
        send_telegram_message(token, chat_id, "⚠️ Akun Admin tidak memerlukan perpanjangan masa aktif.")
        return {'status': 'is_admin'}

    extend_user(user['id'], days=days)
    updated_user = get_user_by_username(username)
    new_exp = str(updated_user.get('expires_at', ''))[:10]
    days_left = updated_user.get('days_left', 0)

    wa_text = (
        f"Halo kak! Langganan SoundCloud Downloader Premium kamu berhasil diperpanjang ✨\n\n"
        f"👤 Username: {username}\n"
        f"➕ Tambahan: +{days} Hari\n"
        f"⏳ Masa Aktif Baru: Sisa {days_left} Hari (Berlaku s/d: {new_exp})\n\n"
        f"Selamat mendownload kembali! Terima kasih! 🙏"
    )

    reply = (
        f"✅ <b>BERHASIL MEMPERPANJANG MEMBER!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Username:</b> <code>{username}</code>\n"
        f"➕ <b>Tambahan:</b> +{days} Hari\n"
        f"⏳ <b>Berlaku s/d:</b> {new_exp} (Sisa {days_left} hari)\n\n"
        f"📋 <b>Format WA (Ketuk untuk Salin):</b>\n"
        f"<code>{wa_text}</code>"
    )
    send_telegram_message(token, chat_id, reply, reply_markup=build_menu_keyboard())
    return {'status': 'ok'}


def cmd_resetpass(token, chat_id, args):
    """Reset user password and revoke active device sessions."""
    if len(args) < 2:
        usage = (
            "⚠️ <b>Format Salah!</b>\n\n"
            "Gunakan format:\n"
            "<code>/resetpass [username] [password_baru]</code>\n\n"
            "Contoh:\n"
            "<code>/resetpass bayu sandibaru99</code>"
        )
        send_telegram_message(token, chat_id, usage)
        return {'status': 'bad_args'}

    username = args[0].strip()
    new_password = args[1].strip()

    user = get_user_by_username(username)
    if not user:
        send_telegram_message(token, chat_id, f"❌ User <b>{username}</b> tidak ditemukan!")
        return {'status': 'not_found'}

    ok, msg = reset_user_password(user['id'], new_password)
    if not ok:
        send_telegram_message(token, chat_id, f"❌ Gagal reset password: {msg}")
        return {'status': 'error', 'message': msg}

    wa_text = (
        f"Halo kak! Password akun SoundCloud Downloader kamu berhasil direset 🔐\n\n"
        f"👤 Username: {username}\n"
        f"🔑 Password Baru: {new_password}\n"
        f"🔗 Link Login: https://soundcloud.my.id/login\n\n"
        f"⚠️ Sesi login di perangkat sebelumnya telah otomatis dikeluarkan untuk keamanan. Silakan login kembali dengan password baru."
    )

    reply = (
        f"✅ <b>KATA SANDI BERHASIL DIRESET!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Username:</b> <code>{username}</code>\n"
        f"🔑 <b>Password Baru:</b> <code>{new_password}</code>\n"
        f"🛡️ <b>Sesi Perangkat Lama:</b> Telah Dikeluarkan Otomatis (Force Logout)\n\n"
        f"📋 <b>Format WA (Ketuk untuk Salin):</b>\n"
        f"<code>{wa_text}</code>"
    )
    send_telegram_message(token, chat_id, reply, reply_markup=build_menu_keyboard())
    return {'status': 'ok'}


def cmd_hapus(token, chat_id, args):
    """Delete a user permanently."""
    if len(args) < 1:
        usage = (
            "⚠️ <b>Format Salah!</b>\n\n"
            "Gunakan format:\n"
            "<code>/hapus [username]</code>\n\n"
            "Contoh:\n"
            "<code>/hapus bayu</code>"
        )
        send_telegram_message(token, chat_id, usage)
        return {'status': 'bad_args'}

    username = args[0].strip()
    user = get_user_by_username(username)
    if not user:
        send_telegram_message(token, chat_id, f"❌ User <b>{username}</b> tidak ditemukan!")
        return {'status': 'not_found'}

    if user.get('is_admin'):
        send_telegram_message(token, chat_id, "⛔ Akun Admin tidak dapat dihapus melalui Telegram!")
        return {'status': 'is_admin'}

    delete_user(user['id'])
    send_telegram_message(
        token, chat_id,
        f"🗑️ <b>USER BERHASIL DIHAPUS!</b>\nAkun member <code>{username}</code> telah dihapus permanen dari sistem.",
        reply_markup=build_menu_keyboard()
    )
    return {'status': 'ok'}


def cmd_status(token, chat_id):
    """Show detailed server health, storage, and token status."""
    users = get_all_users()
    active_count = sum(1 for u in users if u.get('is_active'))
    expired_count = len(users) - active_count

    # Token status
    sys_token = get_setting('soundcloud_oauth_token', '')
    token_status = "🟢 Terhubung (Go+ Aktif)" if sys_token else "🔴 Belum Disetel"

    # Disk usage for temp downloads
    temp_dir = os.environ.get('TEMP_DIR', '/tmp/scdl')
    dir_size_mb = 0.0
    if os.path.exists(temp_dir):
        try:
            total_b = sum(os.path.getsize(os.path.join(temp_dir, f)) for f in os.listdir(temp_dir) if os.path.isfile(os.path.join(temp_dir, f)))
            dir_size_mb = round(total_b / (1024 * 1024), 2)
        except Exception:
            pass

    # Disk free space
    try:
        total, used, free = shutil.disk_usage('/')
        disk_free_gb = round(free / (1024**3), 2)
    except Exception:
        disk_free_gb = "N/A"

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S WIB')

    msg = (
        f"📊 <b>STATUS SISTEM SOUNDCLOUD WEB</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 <b>Waktu:</b> {now_str}\n"
        f"👥 <b>Member Aktif:</b> {active_count} akun\n"
        f"🔴 <b>Member Expired:</b> {expired_count} akun\n"
        f"🔑 <b>Master Token:</b> {token_status}\n"
        f"📁 <b>Cache Download:</b> {dir_size_mb} MB ({temp_dir})\n"
        f"💾 <b>Sisa Storage Server:</b> {disk_free_gb} GB\n"
        f"🌐 <b>Domain:</b> https://soundcloud.my.id\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ <i>Semua layanan berjalan dengan normal.</i>"
    )
    send_telegram_message(token, chat_id, msg, reply_markup=build_menu_keyboard())
    return {'status': 'ok'}


def cmd_log(token, chat_id):
    """Show 10 recent download logs."""
    logs = get_recent_downloads(limit=10)
    if not logs:
        send_telegram_message(token, chat_id, "ℹ️ Belum ada riwayat unduhan di log audit.", reply_markup=build_menu_keyboard())
        return {'status': 'ok'}

    lines = ["📜 <b>10 RIWAYAT DOWNLOAD TERAKHIR:</b>\n━━━━━━━━━━━━━━━━━━━━"]
    for idx, l in enumerate(logs, 1):
        uname = l.get('username') or 'Guest'
        title = l.get('track_title') or 'Lagu'
        fmt = l.get('audio_format') or 'mp3'
        size = l.get('file_size_mb') or 0.0
        time_str = l.get('created_at') or ''
        lines.append(f"{idx}. <b>{uname}</b> — <i>{title[:25]}</i>\n   └ {fmt.upper()} • {size} MB • {time_str}")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    send_telegram_message(token, chat_id, "\n".join(lines), reply_markup=build_menu_keyboard())
    return {'status': 'ok'}


def cmd_token(token, chat_id, new_token=None):
    """View or update SoundCloud Master Go+ OAuth Token."""
    if new_token:
        clean_token = new_token.strip()
        set_setting('soundcloud_oauth_token', clean_token)
        send_telegram_message(
            token, chat_id,
            f"✅ <b>Master Go+ Token Berhasil Diperbarui!</b>\nToken baru: <code>{clean_token}</code>",
            reply_markup=build_menu_keyboard()
        )
        return {'status': 'ok'}

    # View token
    cur_token = get_setting('soundcloud_oauth_token', '')
    if not cur_token:
        msg = (
            "🔑 <b>MASTER GO+ TOKEN:</b>\n\n"
            "🔴 <i>Belum disetel di sistem.</i>\n\n"
            "Untuk mengatur token baru, ketik:\n"
            "<code>/token [token_oauth_kamu]</code>"
        )
    else:
        masked = cur_token[:6] + "..." + cur_token[-4:] if len(cur_token) > 10 else cur_token
        msg = (
            f"🔑 <b>MASTER GO+ TOKEN:</b>\n\n"
            f"Status: 🟢 Aktif\n"
            f"Token: <code>{masked}</code>\n\n"
            f"💡 <i>Untuk mengganti token, ketik:</i>\n"
            f"<code>/token [token_oauth_baru]</code>"
        )
    send_telegram_message(token, chat_id, msg, reply_markup=build_menu_keyboard())
    return {'status': 'ok'}


def cmd_help(token, chat_id):
    """Show guide of all available commands."""
    guide = (
        "📖 <b>PANDUAN PERINTAH BOT ADMIN TELEGRAM</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🟢 <b>/menu</b> atau <b>/start</b>\n"
        "Membuka tombol menu dashboard utama.\n\n"
        "👥 <b>/users</b>\n"
        "Melihat seluruh daftar member dan sisa hari aktif.\n\n"
        "➕ <b>/tambah [user] [pass] [hari]</b>\n"
        "Membuat akun member baru + format siap kirim WA.\n"
        "<i>Contoh:</i> <code>/tambah andi pass123 30</code>\n\n"
        "⏳ <b>/perpanjang [user] [hari]</b>\n"
        "Menambah masa aktif member + format siap kirim WA.\n"
        "<i>Contoh:</i> <code>/perpanjang andi 30</code>\n\n"
        "🔐 <b>/resetpass [user] [pass_baru]</b>\n"
        "Reset password & otomatis logout perangkat lama.\n"
        "<i>Contoh:</i> <code>/resetpass andi baru99</code>\n\n"
        "🗑️ <b>/hapus [user]</b>\n"
        "Menghapus member secara permanen dari database.\n"
        "<i>Contoh:</i> <code>/hapus andi</code>\n\n"
        "📊 <b>/status</b>\n"
        "Cek kesehatan server, sisa storage, dan token Go+.\n\n"
        "📜 <b>/log</b>\n"
        "Melihat 10 riwayat unduhan member terakhir.\n\n"
        "🔑 <b>/token [token_baru]</b>\n"
        "Melihat atau memperbarui Master OAuth Token Go+.\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    send_telegram_message(token, chat_id, guide, reply_markup=build_menu_keyboard())
    return {'status': 'ok'}
