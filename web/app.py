from flask import Flask, render_template, request, jsonify, send_file, after_this_request, session, redirect, url_for
from functools import wraps
from datetime import timedelta
import subprocess
import sys
import os
import tempfile
import shutil
import uuid
import time
import secrets
import re
import threading
import requests as req
from db import (
    init_db, check_user, get_all_users, add_user, delete_user,
    extend_user, reset_user_password, get_setting, set_setting, get_user_status,
    change_admin_password, log_download, get_recent_downloads
)
try:
    from telegram_bot import (
        get_telegram_config,
        set_telegram_config,
        register_webhook,
        delete_webhook,
        handle_telegram_update
    )
except ImportError:
    from web.telegram_bot import (
        get_telegram_config,
        set_telegram_config,
        register_webhook,
        delete_webhook,
        handle_telegram_update
    )

app = Flask(__name__)

# Basic paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(BASE_DIR, 'downloads')
# ProxyFix for Render reverse proxy
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Ensure downloads directory exists
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Guaranteed yt-dlp command using current python runtime
YT_DLP_CMD = [sys.executable, '-m', 'yt_dlp']

def check_soundcloud_token_status(token):
    """Check whether SoundCloud token is active and whether it has Go+ subscription."""
    if not token:
        return {'status': 'empty', 'label': 'Belum disetel (Direct lagu Free saja)', 'plan': None, 'username': None}
    try:
        clean_tok = token.replace('OAuth ', '').replace('oauth ', '').strip()
        r = req.get('https://api-v2.soundcloud.com/me', headers={'Authorization': f'OAuth {clean_tok}', 'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if r.status_code == 200:
            d = r.json()
            plan = d.get('plan')
            username = d.get('username', 'User')
            if plan and ('go' in str(plan).lower() or 'plus' in str(plan).lower() or 'high' in str(plan).lower()):
                return {'status': 'go_plus', 'label': f'SoundCloud Go+ Aktif ({username}) ⭐', 'plan': plan, 'username': username}
            else:
                return {'status': 'free', 'label': f'Akun Standar Free ({username}) — Go+ DRM di-mirror via Smart Mirror 🔀', 'plan': plan or 'Free', 'username': username}
        else:
            return {'status': 'invalid', 'label': 'Token Tidak Valid / Kedaluwarsa ⚠️', 'plan': None, 'username': None}
    except Exception:
        return {'status': 'unknown', 'label': 'Token Tersimpan (Cek status offline)', 'plan': None, 'username': None}

def search_youtube_video_ids(clean_search):
    """Scrape YouTube web search directly with browser headers to bypass datacenter IP bot-check."""
    try:
        import urllib.parse
        clean_q = urllib.parse.quote(clean_search)
        url = f'https://www.youtube.com/results?search_query={clean_q}'
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        r = req.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            vids = list(dict.fromkeys(re.findall(r'\"videoId\":\"([a-zA-Z0-9_-]{11})\"', r.text)))
            if vids:
                return vids[:3]
    except Exception as e:
        app.logger.warning(f"Error scraping YouTube search: {e}")
@app.route('/api/debug-probe')
def debug_probe():
    import urllib.parse
    clean_search = request.args.get('q', 'ROMBE4T Jax s Groove')
    
    clean_q = urllib.parse.quote(clean_search)
    url = f'https://www.youtube.com/results?search_query={clean_q}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    r = req.get(url, headers=headers, timeout=8)
    vids = list(dict.fromkeys(re.findall(r'\"videoId\":\"([a-zA-Z0-9_-]{11})\"', r.text)))
    
    test_vid = vids[0] if vids else 'b-T_G_U4Wac'
    test_url = f'https://www.youtube.com/watch?v={test_vid}'
    cmd = [*YT_DLP_CMD, '--simulate', '--verbose', test_url]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20)
    
    return jsonify({
        'yt_scrape_status': r.status_code,
        'yt_scrape_len': len(r.text),
        'vids': vids[:5],
        'test_url': test_url,
        'ytdl_returncode': p.returncode,
        'ytdl_stdout': p.stdout,
        'ytdl_stderr': p.stderr,
        'has_node': bool(shutil.which('node')),
    })

# Concurrency & Spam Prevention
active_downloads = set()       # Set of active user_ids downloading
last_download_time = {}       # { user_id: timestamp }

def auto_cleanup_storage():
    """Auto-purge temporary download directories older than 30 minutes."""
    try:
        now = time.time()
        for item in os.listdir(DOWNLOADS_DIR):
            item_path = os.path.join(DOWNLOADS_DIR, item)
            try:
                if os.path.isdir(item_path):
                    if now - os.path.getmtime(item_path) > 1800:
                        shutil.rmtree(item_path, ignore_errors=True)
                elif os.path.isfile(item_path) and item.endswith('.zip'):
                    if now - os.path.getmtime(item_path) > 1800:
                        os.remove(item_path)
            except Exception:
                pass
    except Exception:
        pass

# Initialize database
init_db()

# ===================== SECURITY: DYNAMIC CRYPTOGRAPHIC SECRET KEY =====================
def get_secure_secret_key():
    """Retrieve secret key from ENV, or persist a cryptographically secure 64-char key in database."""
    env_key = os.environ.get('SECRET_KEY')
    if env_key and len(env_key.strip()) >= 16:
        return env_key.strip()
    try:
        db_key = get_setting('flask_secret_key')
        if db_key and len(db_key.strip()) >= 32:
            return db_key.strip()
        new_key = secrets.token_hex(32)
        set_setting('flask_secret_key', new_key)
        return new_key
    except Exception as e:
        app.logger.warning(f"Error fetching/saving flask_secret_key: {e}")
        return secrets.token_hex(32)

app.secret_key = get_secure_secret_key()

# ===================== SECURITY: COOKIE & PAYLOAD CONFIG =====================
app.permanent_session_lifetime = timedelta(days=7)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE='Lax',
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,  # Max payload limit 16MB
)

@app.before_request
def make_session_permanent():
    session.permanent = True

# ===================== SECURITY: HTTP SECURITY HEADERS =====================
@app.after_request
def set_security_headers(response):
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    return response

# ===================== SECURITY: RATE LIMITING (#2) =====================
login_attempts = {}  # { ip: { 'count': int, 'first_attempt': float } }
MAX_LOGIN_ATTEMPTS = 15
LOCKOUT_DURATION = 300  # 5 minutes in seconds

def get_client_ip():
    """Get real client IP behind reverse proxy."""
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr

def is_rate_limited(ip):
    """Check if an IP is rate-limited."""
    if ip not in login_attempts:
        return False
    info = login_attempts[ip]
    # Reset if lockout period has expired
    if time.time() - info['first_attempt'] > LOCKOUT_DURATION:
        del login_attempts[ip]
        return False
    return info['count'] >= MAX_LOGIN_ATTEMPTS

def record_failed_login(ip):
    """Record a failed login attempt."""
    if ip not in login_attempts:
        login_attempts[ip] = {'count': 1, 'first_attempt': time.time()}
    else:
        login_attempts[ip]['count'] += 1

def clear_login_attempts(ip):
    """Clear login attempts after successful login."""
    if ip in login_attempts:
        del login_attempts[ip]

# ===================== SECURITY: CSRF PROTECTION (#3) =====================
def generate_csrf_token():
    """Generate a CSRF token and store it in the session."""
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']

def validate_csrf_token():
    """Validate the CSRF token from the form against the session."""
    token = request.form.get('_csrf_token', '')
    if not token or token != session.get('_csrf_token', ''):
        return False
    return True

# Make csrf_token available in all templates
app.jinja_env.globals['csrf_token'] = generate_csrf_token

# ===================== AUTH MIDDLEWARE =====================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = session.get('user_id')
        if not user_id:
            if request.path.startswith('/api/'):
                return jsonify({'error': '⛔ Silakan login terlebih dahulu.'}), 401
            return redirect(url_for('login'))

        is_active, err_msg, user_info = get_user_status(user_id)
        if not is_active:
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({'error': err_msg or '⛔ Masa aktif langganan akun kamu sudah habis (expired). Silakan hubungi admin untuk memperpanjang paket.'}), 403
            return render_template('login.html', error=err_msg or '⛔ Masa aktif langganan akun kamu sudah habis (expired). Silakan hubungi admin untuk memperpanjang paket.')

        # Anti-Account Sharing: 1 Akun 1 Device (enforce for regular members)
        if user_info and not user_info.get('is_admin'):
            db_session_token = user_info.get('session_token')
            if db_session_token and session.get('session_token') != db_session_token:
                session.clear()
                if request.path.startswith('/api/'):
                    return jsonify({'error': '⛔ Akun kamu telah login di perangkat lain atau password direset. Hanya 1 perangkat yang diizinkan aktif.'}), 401
                return render_template('login.html', error='⛔ Akun kamu telah aktif di perangkat lain atau password telah direset. Silakan login kembali.')

        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = session.get('user_id')
        if not user_id or not session.get('is_admin'):
            return redirect(url_for('admin_login'))

        is_active, err_msg, user = get_user_status(user_id)
        if not is_active or not user or not user.get('is_admin'):
            session.clear()
            return redirect(url_for('admin_login'))

        return f(*args, **kwargs)
    return decorated

def csrf_required(f):
    """Decorator to validate CSRF token on POST requests."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method == 'POST':
            if not validate_csrf_token():
                return 'CSRF token tidak valid. Silakan refresh halaman dan coba lagi.', 403
        return f(*args, **kwargs)
    return decorated

# ===================== AUTH ROUTES =====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        ip = get_client_ip()
        
        # Check rate limiting
        if is_rate_limited(ip):
            remaining = int(LOCKOUT_DURATION - (time.time() - login_attempts[ip]['first_attempt']))
            minutes = remaining // 60
            return render_template('login.html', error=f'⛔ Terlalu banyak percobaan login. Coba lagi dalam {minutes} menit.')
        
        if not validate_csrf_token():
            return render_template('login.html', error='⛔ Sesi tidak valid. Silakan refresh halaman.')
        
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        user, error = check_user(username, password)
        if user:
            clear_login_attempts(ip)
            session.clear()
            session.permanent = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_admin'] = bool(user['is_admin'])
            session['session_token'] = user.get('session_token')
            return redirect(url_for('index'))
        else:
            record_failed_login(ip)
            return render_template('login.html', error=error)
    
    return render_template('login.html')

@app.route('/kelola-scdl/login', methods=['GET', 'POST'])
def admin_login():
    if 'user_id' in session and session.get('is_admin'):
        return redirect(url_for('admin'))
    
    if request.method == 'POST':
        ip = get_client_ip()
        
        # Check rate limiting
        if is_rate_limited(ip):
            remaining = int(LOCKOUT_DURATION - (time.time() - login_attempts[ip]['first_attempt']))
            minutes = remaining // 60
            return render_template('admin_login.html', error=f'⛔ Terlalu banyak percobaan login. Coba lagi dalam {minutes} menit.')
        
        if not validate_csrf_token():
            return render_template('admin_login.html', error='⛔ Sesi tidak valid. Silakan refresh halaman.')
        
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        user, error = check_user(username, password)
        if user and user['is_admin']:
            clear_login_attempts(ip)
            session.clear()
            session.permanent = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_admin'] = True
            session['session_token'] = user.get('session_token')
            return redirect(url_for('admin'))
        else:
            record_failed_login(ip)
            return render_template('admin_login.html', error='Username atau password admin salah')
    
    return render_template('admin_login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

def get_disk_info():
    """Get disk usage info for downloads folder and VPS."""
    # Downloads folder size
    total_size = 0
    file_count = 0
    for dirpath, dirnames, filenames in os.walk(DOWNLOADS_DIR):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.exists(fp):
                total_size += os.path.getsize(fp)
                file_count += 1
    
    # VPS disk usage
    import shutil as sh
    disk = sh.disk_usage('/')
    
    return {
        'downloads_size_mb': round(total_size / (1024 * 1024), 1),
        'downloads_files': file_count,
        'disk_total_gb': round(disk.total / (1024**3), 1),
        'disk_used_gb': round(disk.used / (1024**3), 1),
        'disk_free_gb': round(disk.free / (1024**3), 1),
        'disk_percent': round((disk.used / disk.total) * 100, 1)
    }

def get_admin_context():
    """Helper to supply full template context for admin views."""
    cookies_path = os.path.join(os.path.dirname(__file__), 'cookies.txt')
    cookies_content = ''
    has_cookies = False
    
    # Check disk first
    if os.path.exists(cookies_path) and os.path.getsize(cookies_path) > 10:
        has_cookies = True
        try:
            with open(cookies_path, 'r', encoding='utf-8') as f:
                cookies_content = f.read()
        except Exception:
            pass
    else:
        # Check database (persistent across Render redeploys)
        db_cookies = get_setting('youtube_cookies', '')
        if db_cookies and len(db_cookies) > 10:
            has_cookies = True
            cookies_content = db_cookies
            try:
                with open(cookies_path, 'w', encoding='utf-8') as f:
                    f.write(db_cookies)
            except Exception:
                pass

    system_token = get_setting('soundcloud_oauth_token', '') or ''
    token_status = check_soundcloud_token_status(system_token)
    telegram_config = get_telegram_config()
    return {
        'users': get_all_users(),
        'disk': get_disk_info(),
        'has_cookies': has_cookies,
        'cookies_content': cookies_content,
        'system_token': system_token,
        'token_status': token_status,
        'recent_downloads': get_recent_downloads(15),
        'telegram_config': telegram_config
    }

# ===================== ADMIN ROUTES =====================

@app.route('/kelola-scdl')
@admin_required
def admin():
    return render_template('admin.html', **get_admin_context())

@app.route('/kelola-scdl/token', methods=['POST'])
@admin_required
@csrf_required
def admin_save_token():
    token_val = request.form.get('system_token', '').strip()
    if token_val.lower().startswith('oauth '):
        token_val = token_val[6:].strip()
    set_setting('soundcloud_oauth_token', token_val)
    if token_val:
        st = check_soundcloud_token_status(token_val)
        if st['status'] == 'go_plus':
            msg = f"✅ Master SoundCloud Go+ Token tersimpan! Status: {st['label']}"
        elif st['status'] == 'free':
            msg = f"ℹ️ Token tersimpan! Status: {st['label']}. (Lagu Go+ DRM di-handle otomatis via Smart Mirror)."
        else:
            msg = f"⚠️ Token tersimpan, namun SoundCloud merespons: {st['label']}."
    else:
        msg = '✅ Master Token dikosongkan.'
    return render_template('admin.html', **get_admin_context(), message=msg)

@app.route('/kelola-scdl/change-password', methods=['POST'])
@admin_required
@csrf_required
def admin_change_password():
    user_id = session.get('user_id')
    old_password = request.form.get('old_password', '').strip()
    new_password = request.form.get('new_password', '').strip()
    confirm_password = request.form.get('confirm_password', '').strip()
    
    if not old_password or not new_password:
        return render_template('admin.html', **get_admin_context(), error='❌ Password lama dan baru wajib diisi')
    if new_password != confirm_password:
        return render_template('admin.html', **get_admin_context(), error='❌ Konfirmasi password baru tidak cocok')
    if len(new_password) < 6:
        return render_template('admin.html', **get_admin_context(), error='❌ Password baru minimal 6 karakter')
        
    success, msg = change_admin_password(user_id, old_password, new_password)
    if success:
        return render_template('admin.html', **get_admin_context(), message='✅ ' + msg)
    else:
        return render_template('admin.html', **get_admin_context(), error='❌ ' + msg)

@app.route('/kelola-scdl/add', methods=['POST'])
@admin_required
@csrf_required
def admin_add():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    days_val = request.form.get('days', '30').strip()
    
    if not re.match(r'^[a-zA-Z0-9_.-]{3,30}$', username):
        return render_template('admin.html', **get_admin_context(), error='❌ Username hanya boleh berisi huruf, angka, titik, strip (-), dan underscore (_), panjang 3-30 karakter')
        
    try:
        days = int(days_val)
        if days < 1 or days > 3650:
            return render_template('admin.html', **get_admin_context(), error='❌ Masa aktif harus antara 1 sampai 3650 hari')
    except (ValueError, TypeError):
        return render_template('admin.html', **get_admin_context(), error='❌ Format jumlah hari tidak valid')
        
    if len(password) < 4:
        return render_template('admin.html', **get_admin_context(), error='❌ Password minimal 4 karakter')
    
    success, msg = add_user(username, password, days=days)
    if success:
        return render_template('admin.html', **get_admin_context(), message=f'✅ {msg}')
    else:
        return render_template('admin.html', **get_admin_context(), error=f'❌ {msg}')

@app.route('/kelola-scdl/delete', methods=['POST'])
@admin_required
@csrf_required
def admin_delete():
    try:
        user_id = int(request.form.get('user_id', 0))
    except (ValueError, TypeError):
        return render_template('admin.html', **get_admin_context(), error='❌ ID user tidak valid')
    delete_user(user_id)
    return render_template('admin.html', **get_admin_context(), message='✅ User berhasil dihapus')

@app.route('/kelola-scdl/extend', methods=['POST'])
@admin_required
@csrf_required
def admin_extend():
    try:
        user_id = int(request.form.get('user_id', 0))
        days = int(request.form.get('days', 30))
        if days < 1 or days > 3650:
            return render_template('admin.html', **get_admin_context(), error='❌ Durasi perpanjangan harus 1 sampai 3650 hari')
    except (ValueError, TypeError):
        return render_template('admin.html', **get_admin_context(), error='❌ ID user atau format hari tidak valid')
    extend_user(user_id, days)
    return render_template('admin.html', **get_admin_context(), message=f'✅ Langganan diperpanjang {days} hari')

@app.route('/kelola-scdl/reset-password', methods=['POST'])
@admin_required
@csrf_required
def admin_reset_password():
    try:
        user_id = int(request.form.get('user_id', 0))
    except (ValueError, TypeError):
        return render_template('admin.html', **get_admin_context(), error='❌ ID user tidak valid')
    new_password = request.form.get('new_password', '').strip()
    
    if len(new_password) < 4:
        return render_template('admin.html', **get_admin_context(), error='❌ Password minimal 4 karakter')
        
    success, msg = reset_user_password(user_id, new_password)
    if success:
        return render_template('admin.html', **get_admin_context(), message='✅ ' + msg)
    else:
        return render_template('admin.html', **get_admin_context(), error='❌ ' + msg)

@app.route('/kelola-scdl/cookies', methods=['POST'])
@admin_required
@csrf_required
def admin_save_cookies():
    content = request.form.get('cookies_content', '').strip()
    cookies_path = os.path.join(os.path.dirname(__file__), 'cookies.txt')
    if content:
        set_setting('youtube_cookies', content)
        with open(cookies_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return render_template('admin.html', **get_admin_context(), message='✅ Cookies YouTube berhasil disimpan di Database & Server! Semua lagu DRM sekarang bebas bot-check.')
    else:
        set_setting('youtube_cookies', '')
        if os.path.exists(cookies_path):
            os.remove(cookies_path)
        return render_template('admin.html', **get_admin_context(), message='✅ Cookies YouTube telah dikosongkan.')

@app.route('/kelola-scdl/cleanup', methods=['POST'])
@admin_required
@csrf_required
def admin_cleanup():
    """Delete all files in the downloads folder."""
    cleaned = 0
    for item in os.listdir(DOWNLOADS_DIR):
        item_path = os.path.join(DOWNLOADS_DIR, item)
        try:
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)
            cleaned += 1
        except Exception:
            pass
    
    return render_template('admin.html', **get_admin_context(),
                           message=f'🧹 Berhasil membersihkan {cleaned} item dari storage')

@app.route('/kelola-scdl/telegram', methods=['POST'])
@admin_required
@csrf_required
def admin_save_telegram():
    action = request.form.get('action', 'save')

    if action == 'disconnect':
        config = get_telegram_config()
        if config.get('token'):
            delete_webhook(config['token'])
        set_telegram_config('', '')
        return render_template('admin.html', **get_admin_context(),
                               message='🔌 Koneksi Telegram Bot berhasil diputuskan.')

    token = request.form.get('telegram_bot_token', '').strip()
    admin_id = request.form.get('telegram_admin_id', '').strip()

    # Smart auto-detection if user accidentally swapped fields
    if ':' in admin_id and ':' not in token:
        token, admin_id = admin_id, token
    elif ':' not in token and token.isdigit():
        cur_cfg = get_telegram_config()
        if cur_cfg.get('token') and ':' in cur_cfg['token']:
            admin_id = token
            token = cur_cfg['token']

    if not token or ':' not in token:
        return render_template('admin.html', **get_admin_context(),
                               error='❌ Token Bot tidak valid! Pastikan menyalin Token dari @BotFather (mengandung tanda titik dua ":").')

    set_telegram_config(token, admin_id)

    # Automatically register webhook with Telegram
    webhook_url = "https://soundcloud.my.id/api/telegram/webhook"
    ok, status_msg = register_webhook(token, webhook_url)

    if ok:
        msg = f'🤖 Telegram Bot berhasil terhubung & Webhook aktif! ({status_msg}). Silakan buka bot Anda di Telegram dan ketik /start atau /menu.'
        return render_template('admin.html', **get_admin_context(), message=msg)
    else:
        err = f'⚠️ Konfigurasi disimpan, namun registrasi webhook Telegram gagal: {status_msg}'
        return render_template('admin.html', **get_admin_context(), error=err)


@app.route('/api/telegram/webhook', methods=['POST'])
def telegram_webhook():
    """Telegram Webhook endpoint (Public receiver for Telegram Bot API)."""
    update = request.get_json(silent=True, force=True)
    if not update:
        return jsonify({'error': 'Invalid payload, JSON required'}), 400

    try:
        res = handle_telegram_update(update)
        return jsonify({'status': 'ok', 'result': res}), 200
    except Exception as e:
        app.logger.error(f"[Telegram Webhook] Error handling update: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 200

# ===================== HEALTH CHECK & KEEP-ALIVE =====================

@app.route('/health')
def health_check():
    """Ultra-fast, zero-overhead health check endpoint for keep-alive monitoring."""
    return jsonify({
        'status': 'ok',
        'service': 'scdl-web',
        'timestamp': int(time.time())
    }), 200

def _keep_alive_worker():
    """Background thread that pings public domain every 8 minutes to prevent Render idle sleep."""
    time.sleep(30)
    target_url = "https://soundcloud.my.id/health"
    while True:
        try:
            req.get(target_url, timeout=10, headers={'User-Agent': 'SCDL-KeepAlive/1.0'})
        except Exception:
            pass
        time.sleep(480)

# Start background pinger
threading.Thread(target=_keep_alive_worker, daemon=True).start()

# ===================== MAIN ROUTES =====================

@app.route('/')
@login_required
def index():
    user_id = session.get('user_id')
    saved_token = ''
    days_left = None
    expires_date = None
    try:
        _, _, user_info = get_user_status(user_id)
        if user_info and not user_info.get('is_admin'):
            days_left = user_info.get('days_left')
            exp_val = user_info.get('expires_at')
            if exp_val:
                expires_date = str(exp_val)[:10]
        from db import get_db
        conn = get_db()
        user = conn.execute('SELECT oauth_token FROM users WHERE id = ?', (user_id,)).fetchone()
        conn.close()
        if user and user['oauth_token']:
            saved_token = user['oauth_token']
    except Exception as e:
        app.logger.error(f"Error loading index data: {e}")
    return render_template('index.html', saved_token=saved_token, days_left=days_left, expires_date=expires_date)


@app.route('/api/save_token', methods=['POST'])
@login_required
def save_token():
    data = request.get_json(silent=True) or {}
    auth_token = str(data.get('auth_token', '')).strip()
    user_id = session.get('user_id')
    
    if not auth_token:
        return jsonify({'error': 'Token tidak boleh kosong'}), 400
    if len(auth_token) > 500:
        return jsonify({'error': 'Format token tidak valid'}), 400
        
    try:
        from db import get_db
        conn = get_db()
        conn.execute('UPDATE users SET oauth_token = ? WHERE id = ?', (auth_token, user_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'Token berhasil disimpan ke akun kamu'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/preview', methods=['POST'])
@login_required
def preview_track():
    from urllib.parse import urlparse
    data = request.get_json(silent=True) or {}
    url = str(data.get('url', '')).strip()
    if not url:
        return jsonify({'error': 'URL tidak boleh kosong'}), 400
    
    if not (url.startswith('https://') or url.startswith('http://')):
        return jsonify({'error': 'URL harus diawali http:// atau https://'}), 400
        
    try:
        parsed_url = urlparse(url)
        hostname = (parsed_url.hostname or '').lower()
        if not (hostname == 'soundcloud.com' or hostname.endswith('.soundcloud.com')):
            return jsonify({'error': 'Hanya link resmi SoundCloud yang didukung'}), 400
    except Exception:
        return jsonify({'error': 'Format URL tidak valid'}), 400

    if 'on.soundcloud.com' in url:
        try:
            resolved = req.get(url, allow_redirects=True, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            if resolved.url and 'soundcloud.com' in resolved.url:
                url = resolved.url
        except Exception:
            pass

    try:
        r = req.get(f'https://soundcloud.com/oembed?url={url}&format=json', headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            iframe_src = ''
            html_raw = data.get('html', '')
            src_match = re.search(r'src="([^"]+)"', html_raw)
            if src_match:
                iframe_src = src_match.group(1).replace('visual=true', 'visual=false')
            
            clean_title = data.get('title', '')
            author = data.get('author_name', '')
            if author and clean_title.endswith(f" by {author}"):
                clean_title = clean_title[:-len(f" by {author}")]

            return jsonify({
                'success': True,
                'title': clean_title,
                'artist': author,
                'author_url': data.get('author_url', ''),
                'artwork_url': data.get('thumbnail_url', ''),
                'widget_url': iframe_src,
                'is_playlist': '/playlists/' in iframe_src or '/sets/' in url
            })
        else:
            return jsonify({'error': 'Tidak dapat memuat metadata preview lagu.'}), 404
    except Exception as e:
        return jsonify({'error': f'Gagal memuat preview: {str(e)}'}), 500


@app.route('/api/download', methods=['POST'])
@login_required
def download():
    import requests as req
    from urllib.parse import urlparse

    user_id = session.get('user_id')
    now = time.time()

    # 1. Anti-spam cooldown (3 seconds)
    if user_id in last_download_time and (now - last_download_time[user_id]) < 3.0:
        wait_sec = round(3.0 - (now - last_download_time[user_id]), 1)
        return jsonify({'error': f'⏳ Harap tunggu {wait_sec} detik sebelum memulai unduhan berikutnya.'}), 429

    # 2. Concurrency lock (1 active download per user)
    if user_id in active_downloads:
        return jsonify({'error': '⏳ Kamu sedang memiliki proses unduhan yang berjalan. Tunggu hingga selesai ya.'}), 429

    # Register active download
    active_downloads.add(user_id)
    auto_cleanup_storage()

    data = request.get_json(silent=True) or {}
    url = str(data.get('url', '')).strip()
    auth_token = str(data.get('auth_token', '')).strip() if data.get('auth_token') else ''
    raw_format = str(data.get('format', 'mp3_320')).strip().lower()
    
    # Map bitrates & formats
    audio_quality = None
    if raw_format in ('mp3_320', 'mp3'):
        audio_format = 'mp3'
        audio_quality = '320K'
    elif raw_format == 'mp3_192':
        audio_format = 'mp3'
        audio_quality = '192K'
    elif raw_format == 'mp3_128':
        audio_format = 'mp3'
        audio_quality = '128K'
    elif raw_format in ('flac', 'wav', 'aac', 'ogg', 'opus', 'm4a'):
        audio_format = raw_format
        audio_quality = None
    else:
        audio_format = 'mp3'
        audio_quality = '320K'
    
    # Map format for yt-dlp compatibility ('ogg' uses 'vorbis' codec in yt-dlp)
    ytdl_format = 'vorbis' if audio_format == 'ogg' else audio_format
    
    if not url:
        return jsonify({'error': 'URL wajib diisi'}), 400
        
    # Prevent CLI flag injection and oversize inputs
    if url.startswith('-') or len(url) > 1000:
        return jsonify({'error': 'Format URL tidak valid'}), 400
        
    if not (url.startswith('https://') or url.startswith('http://')):
        return jsonify({'error': 'URL harus diawali dengan http:// atau https://'}), 400
        
    # Strictly enforce SoundCloud domain (prevents SSRF and third-party abuse)
    try:
        parsed_url = urlparse(url)
        hostname = (parsed_url.hostname or '').lower()
        if not (hostname == 'soundcloud.com' or hostname.endswith('.soundcloud.com')):
            return jsonify({'error': 'Hanya link resmi SoundCloud (soundcloud.com atau on.soundcloud.com) yang didukung'}), 400
    except Exception:
        return jsonify({'error': 'Format URL tidak valid'}), 400
        
    # Resolve shortlink if on.soundcloud.com
    if 'on.soundcloud.com' in url:
        try:
            resolved = req.get(url, allow_redirects=True, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            if resolved.url and 'soundcloud.com' in resolved.url:
                url = resolved.url
        except Exception as e:
            app.logger.warning(f"Failed to resolve shortlink: {e}")

    # Clean and resolve auth_token
    clean_token = auth_token.strip() if auth_token else ''
    if not clean_token:
        # Priority 1: System Master Go+ Token from database settings
        try:
            sys_token = get_setting('soundcloud_oauth_token', '')
            if sys_token:
                clean_token = sys_token.strip()
        except Exception as e:
            app.logger.warning(f"Failed to fetch system soundcloud_oauth_token: {e}")

    if not clean_token:
        # Priority 2: User personal token
        try:
            from db import get_db
            conn = get_db()
            user_row = conn.execute('SELECT oauth_token FROM users WHERE id = ?', (session.get('user_id'),)).fetchone()
            conn.close()
            if user_row and user_row['oauth_token']:
                clean_token = user_row['oauth_token'].strip()
        except Exception:
            pass

    if clean_token.lower().startswith('oauth '):
        clean_token = clean_token[6:].strip()

    resolve_debug = 'not_checked'
    # Auto-resolve truncated or incomplete SoundCloud URLs
    if 'soundcloud.com/' in url and 'on.soundcloud.com' not in url:
        parsed_parts = [p for p in url.rstrip('/').split('?')[0].split('/') if p and not p.startswith('http') and 'soundcloud.com' not in p]
        resolve_debug = f'parts: {parsed_parts}'
        if len(parsed_parts) >= 2:
            uploader = parsed_parts[0]
            slug = parsed_parts[1]
            try:
                headers = {'User-Agent': 'Mozilla/5.0'}
                if clean_token:
                    headers['Authorization'] = f'OAuth {clean_token}'
                check_r = req.get(f'https://api-v2.soundcloud.com/resolve?url={url}', headers=headers, timeout=10)
                resolve_debug += f' | check_r: {check_r.status_code}'
                if check_r.status_code == 404:
                    matched = False
                    # 1. Search uploader's tracks
                    u_res = req.get(f'https://api-v2.soundcloud.com/resolve?url=https://soundcloud.com/{uploader}', headers=headers, timeout=10)
                    resolve_debug += f' | u_res: {u_res.status_code}'
                    if u_res.status_code == 200:
                        uid = u_res.json().get('id')
                        tr_res = req.get(f'https://api-v2.soundcloud.com/users/{uid}/tracks?limit=30', headers=headers, timeout=10)
                        resolve_debug += f' | tr_res: {tr_res.status_code}'
                        if tr_res.status_code == 200:
                            for t in tr_res.json().get('collection', []):
                                perm = t.get('permalink', '')
                                if perm.startswith(slug) or slug in perm:
                                    url = t.get('permalink_url', url)
                                    resolve_debug += f' | matched: {url}'
                                    matched = True
                                    break
                    # 2. General tracks search fallback
                    if not matched:
                        s_res = req.get(f'https://api-v2.soundcloud.com/search/tracks?q={uploader} {slug}', headers=headers, timeout=10)
                        if s_res.status_code == 200:
                            coll = s_res.json().get('collection', [])
                            if coll:
                                url = coll[0].get('permalink_url', url)
                                resolve_debug += f' | search_matched: {url}'
            except Exception as e:
                resolve_debug += f' | exception: {e}'

    # Create a unique temporary directory for this download
    job_id = str(uuid.uuid4())
    job_dir = os.path.join(DOWNLOADS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    
    try:
        def find_valid_audio(d):
            valid_exts = ('.mp3', '.m4a', '.opus', '.flac', '.wav', '.aac', '.ogg')
            return [
                os.path.join(d, f) for f in os.listdir(d)
                if any(f.lower().endswith(ext) for ext in valid_exts)
                and not f.endswith('.part') and not f.endswith('.ytdl')
                and os.path.isfile(os.path.join(d, f))
                and os.path.getsize(os.path.join(d, f)) > 10000
            ]

        cmd = [
            *YT_DLP_CMD,
            '-x', '--audio-format', ytdl_format,
            '--socket-timeout', '10',
            '--retries', '1',
            '--fragment-retries', '1',
            '--concurrent-fragments', '5',
            '--match-filter', "format_id !*= 'preview'",
        ]
        if audio_quality:
            cmd.extend(['--audio-quality', audio_quality])
        cmd.extend([
            '--max-downloads', '15',
            '--embed-metadata',
            '-o', os.path.join(job_dir, '%(title)s.%(ext)s'),
        ])
        
        # Only embed thumbnail for containers that support it natively (mp3, flac, m4a)
        if audio_format in ('mp3', 'flac', 'm4a'):
            cmd.extend(['--embed-thumbnail', '--convert-thumbnails', 'jpg'])
        
        if clean_token:
            cmd.extend([
                '--username', 'oauth',
                '--password', clean_token,
                '--add-header', f'Authorization: OAuth {clean_token}',
            ])

        cmd.extend(['--', url])

        # Run the command with timeout (240s for long mixes or playlists)
        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=240)
        
        # Check if direct SoundCloud download hit DRM protection or was skipped because preview
        is_drm = False
        err_output = process.stderr or process.stdout or ''
        if 'DRM' in err_output or 'preview' in err_output or 'does not pass filter' in (process.stdout or ''):
            is_drm = True

        # If token caused failure on free/public track, retry once without token ONLY if not DRM
        if (process.returncode != 0 or not find_valid_audio(job_dir)) and clean_token and not is_drm:
            cmd_no_tok = [
                *YT_DLP_CMD,
                '-x', '--audio-format', ytdl_format,
                '--socket-timeout', '10',
                '--retries', '1',
                '--fragment-retries', '1',
                '--concurrent-fragments', '5',
                '--match-filter', "format_id !*= 'preview'",
            ]
            if audio_quality:
                cmd_no_tok.extend(['--audio-quality', audio_quality])
            cmd_no_tok.extend([
                '--max-downloads', '15',
                '--embed-metadata',
                '-o', os.path.join(job_dir, '%(title)s.%(ext)s'),
            ])
            if audio_format in ('mp3', 'flac', 'm4a'):
                cmd_no_tok.extend(['--embed-thumbnail', '--convert-thumbnails', 'jpg'])
            cmd_no_tok.extend(['--', url])
            proc_no_tok = subprocess.run(cmd_no_tok, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=240)
            if proc_no_tok.returncode == 0 and find_valid_audio(job_dir):
                process = proc_no_tok

        if process.returncode != 0 or not find_valid_audio(job_dir):
            err_output = process.stderr or process.stdout or ''
            app.logger.info(f"Direct SoundCloud produced no valid audio (returncode={process.returncode}, is_drm={is_drm}), activating Smart Hybrid Mirror (YouTube)...")
            
            fallback_success = False
            meta = None
            
            # Step 1: Fetch official metadata & artwork from SoundCloud API
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                if clean_token:
                    headers['Authorization'] = f'OAuth {clean_token}'
                
                # Check if yt-dlp extracted track ID in error message (e.g. from redirect or truncated URL)
                track_id_match = re.search(r'\[soundcloud\]\s+(\d+):', err_output)
                if track_id_match:
                    tid = track_id_match.group(1)
                    tr = req.get(f'https://api-v2.soundcloud.com/tracks/{tid}', headers=headers, timeout=10)
                    if tr.status_code == 200:
                        d = tr.json()
                        title = d.get('title', '').strip()
                        artist = d.get('user', {}).get('username', '').strip()
                        artwork = d.get('artwork_url') or d.get('user', {}).get('avatar_url')
                        if artwork:
                            artwork = artwork.replace('-large.', '-t500x500.')
                        dur_ms = d.get('full_duration') or d.get('duration') or 0
                        meta = {'title': title, 'artist': artist, 'artwork_url': artwork, 'duration_sec': int(dur_ms // 1000)}

                if not meta:
                    api_url = f'https://api-v2.soundcloud.com/resolve?url={url}'
                    r = req.get(api_url, headers=headers, timeout=10)
                    if r.status_code == 200:
                        d = r.json()
                        title = d.get('title', '').strip()
                        artist = d.get('user', {}).get('username', '').strip()
                        artwork = d.get('artwork_url') or d.get('user', {}).get('avatar_url')
                        if artwork:
                            artwork = artwork.replace('-large.', '-t500x500.')
                        dur_ms = d.get('full_duration') or d.get('duration') or 0
                        meta = {'title': title, 'artist': artist, 'artwork_url': artwork, 'duration_sec': int(dur_ms // 1000)}
                    elif '-' in url:
                        # Possibly truncated URL, search SoundCloud search API
                        slug = url.rstrip('/').split('/')[-1].replace('-', ' ').strip()
                        uploader = url.rstrip('/').split('/')[-2] if len(url.rstrip('/').split('/')) >= 2 else ''
                        search_query = f"{uploader} {slug}".strip()
                        sr = req.get(f'https://api-v2.soundcloud.com/search/tracks?q={search_query}', headers=headers, timeout=10)
                        if sr.status_code == 200:
                            items = sr.json().get('collection', [])
                            if items:
                                first = items[0]
                                title = first.get('title', '').strip()
                                artist = first.get('user', {}).get('username', '').strip()
                                artwork = first.get('artwork_url') or first.get('user', {}).get('avatar_url')
                                if artwork:
                                    artwork = artwork.replace('-large.', '-t500x500.')
                                dur_ms = first.get('full_duration') or first.get('duration') or 0
                                meta = {'title': title, 'artist': artist, 'artwork_url': artwork, 'duration_sec': int(dur_ms // 1000)}
            except Exception as e:
                app.logger.warning(f"Error fetching SoundCloud metadata for mirror: {e}")
            
            # Step 1b: If API failed (e.g. 401 without Go+), scrape track page HTML directly
            if not meta or not meta.get('title'):
                try:
                    page_r = req.get(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}, timeout=6)
                    if page_r.status_code == 200:
                        og_title = re.search(r'<meta property="og:title" content="([^"]+)"', page_r.text)
                        og_image = re.search(r'<meta property="og:image" content="([^"]+)"', page_r.text)
                        if og_title:
                            raw_title = og_title.group(1).replace('&#39;', "'").replace('&amp;', '&').replace('&quot;', '"').strip()
                            parts = raw_title.split(' by ', 1)
                            if len(parts) == 2:
                                meta = {'title': parts[0].strip(), 'artist': parts[1].strip(), 'artwork_url': og_image.group(1) if og_image else None, 'duration_sec': 0}
                            else:
                                meta = {'title': raw_title, 'artist': '', 'artwork_url': og_image.group(1) if og_image else None, 'duration_sec': 0}
                except Exception as e_page:
                    app.logger.warning(f"Error scraping SoundCloud page HTML: {e_page}")

            if not meta or not meta.get('title'):
                parts = [p for p in url.rstrip('/').split('/') if p and not p.startswith('http') and 'soundcloud.com' not in p]
                uploader = ''
                if len(parts) >= 2:
                    uploader = parts[-3] if parts[-2] == 'sets' and len(parts) >= 3 else parts[-2]
                slug = parts[-1].replace('-', ' ') if parts else ''
                meta = {'title': slug.title(), 'artist': uploader.title() if uploader else '', 'artwork_url': None, 'duration_sec': 0}
                
            # Step 2: Fetch audio via search mirror with Smart Duration Matcher
            search_term = f"{meta['artist']} {meta['title']}".strip()
            clean_search = ' '.join(re.sub(r'[\'\"()\[\]{}|:;<>*?]', ' ', search_term).split())
            duration_sec = meta.get('duration_sec', 0)
            if clean_search:
                shutil.rmtree(job_dir, ignore_errors=True)
                os.makedirs(job_dir, exist_ok=True)

                cookies_file = None
                for cand_path in [
                    os.path.join(os.path.dirname(__file__), 'cookies.txt'),
                    os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cookies.txt'),
                    '/app/cookies.txt'
                ]:
                    if os.path.exists(cand_path) and os.path.getsize(cand_path) > 10:
                        cookies_file = cand_path
                        break
                
                # Check DB for cookies if not found on ephemeral disk
                if not cookies_file:
                    db_cookies = get_setting('youtube_cookies', '')
                    if db_cookies and len(db_cookies) > 10:
                        cand_path = os.path.join(os.path.dirname(__file__), 'cookies.txt')
                        try:
                            with open(cand_path, 'w', encoding='utf-8') as f:
                                f.write(db_cookies)
                            cookies_file = cand_path
                        except Exception:
                            pass

                fallback_proc = None

                def build_yt_cmd(target):
                    c = [
                        *YT_DLP_CMD,
                        '--no-playlist',
                        '--max-downloads', '1',
                        '--ignore-errors',
                        '--no-abort-on-error',
                        '--socket-timeout', '10',
                        '--retries', '1',
                        '--fragment-retries', '1',
                        '--concurrent-fragments', '5',
                        '-f', '18/bestaudio/best',
                        '-x', '--audio-format', ytdl_format,
                        '--add-header', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                    ]
                    if shutil.which('node'):
                        c.extend(['--js-runtimes', 'node', '--remote-components', 'ejs:github'])
                    if cookies_file:
                        c.extend(['--cookies', cookies_file])
                    if audio_quality:
                        c.extend(['--audio-quality', audio_quality])
                    c.extend([
                        '--embed-metadata',
                        '-o', os.path.join(job_dir, '%(title)s.%(ext)s'),
                    ])
                    if audio_format in ('mp3', 'flac', 'm4a'):
                        c.extend(['--embed-thumbnail', '--convert-thumbnails', 'jpg'])
                    c.append(target)
                    return c

                # Primary attempt: Direct YouTube video URLs from web scrape (bypasses datacenter bot-check)
                direct_vids = search_youtube_video_ids(clean_search)
                app.logger.info(f"Direct web scrape YouTube search found {len(direct_vids)} candidates for '{clean_search}'")

                for vid in direct_vids:
                    vid_url = f"https://www.youtube.com/watch?v={vid}"
                    yt_cmd = build_yt_cmd(vid_url)
                    if duration_sec > 10:
                        yt_cmd.insert(-1, '--match-filter')
                        yt_cmd.insert(-1, f"duration >= {max(1, duration_sec - 15)} & duration <= {duration_sec + 15}")
                    fallback_proc = subprocess.run(yt_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
                    if find_valid_audio(job_dir):
                        break

                # If duration filter eliminated matches, retry first candidate without duration filter
                if not find_valid_audio(job_dir) and direct_vids:
                    vid_url = f"https://www.youtube.com/watch?v={direct_vids[0]}"
                    yt_cmd = build_yt_cmd(vid_url)
                    fallback_proc = subprocess.run(yt_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)

                # Secondary fallback: only if direct scraping failed
                if not find_valid_audio(job_dir):
                    search_query = f"ytsearch2:{clean_search}"
                    yt_cmd = build_yt_cmd(search_query)
                    fallback_proc = subprocess.run(yt_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
                
                valid_files = find_valid_audio(job_dir)
                if valid_files:
                    fallback_success = True
                    target_file = valid_files[0]
                    # Embed official SoundCloud metadata and artwork into MP3
                    if target_file.endswith('.mp3'):
                        try:
                            from mutagen.id3 import ID3, APIC, TIT2, TPE1, ID3NoHeaderError
                            try:
                                tags = ID3(target_file)
                            except ID3NoHeaderError:
                                tags = ID3()
                            if meta.get('title'):
                                tags.add(TIT2(encoding=3, text=meta['title']))
                            if meta.get('artist'):
                                tags.add(TPE1(encoding=3, text=meta['artist']))
                            if meta.get('artwork_url'):
                                try:
                                    img_data = req.get(meta['artwork_url'], timeout=10).content
                                    tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=img_data))
                                except Exception:
                                    pass
                            tags.save(target_file)
                        except Exception as em:
                            app.logger.warning(f"Error tagging MP3: {em}")
                    
                    # Rename to match official SoundCloud title
                    clean_name = re.sub(r'[/\\?%*:|"<>]', '_', meta['title'])
                    final_path = os.path.join(job_dir, f"{clean_name}.{audio_format}")
                    if target_file != final_path:
                        try:
                            os.rename(target_file, final_path)
                        except Exception:
                            pass
            
            if not fallback_success:
                shutil.rmtree(job_dir, ignore_errors=True)
                mirror_err = ''
                if fallback_proc:
                    mirror_err = (fallback_proc.stderr or fallback_proc.stdout or '').strip()
                if 'how-do-i-pass-cookies-to-yt-dlp' in mirror_err or 'confirm you\'re not a bot' in mirror_err:
                    msg = 'Lagu ini memiliki enkripsi DRM SoundCloud Go+ dan dibatasi bot-check YouTube di server. Admin perlu memperbarui cookies YouTube di Admin Panel (/kelola-scdl).'
                else:
                    clean_err = [line for line in err_output.splitlines() if line.startswith('ERROR:')]
                    msg = clean_err[-1] if clean_err else (err_output.splitlines()[-1] if err_output.splitlines() else 'Gagal mengunduh audio')
                return jsonify({'error': f'Download gagal: {msg}'}), 500
            
        # Find the downloaded file(s)
        downloaded_files = os.listdir(job_dir)
        
        if not downloaded_files:
            shutil.rmtree(job_dir, ignore_errors=True)
            return jsonify({'error': 'Tidak ada file yang terdownload. URL mungkin tidak valid.'}), 404
            
        # Helper to find audio files specifically
        valid_audio_exts = ('.mp3', '.flac', '.wav', '.aac', '.ogg', '.opus', '.m4a')
        audio_files = []
        all_other_files = []
        for root, _, files in os.walk(job_dir):
            for filename in files:
                fpath = os.path.join(root, filename)
                if any(filename.lower().endswith(ext) for ext in valid_audio_exts) and not filename.endswith('.part') and not filename.endswith('.ytdl'):
                    audio_files.append(fpath)
                else:
                    all_other_files.append(fpath)
                    
        files_to_check = audio_files if audio_files else all_other_files
        
        if not files_to_check:
            shutil.rmtree(job_dir, ignore_errors=True)
            return jsonify({'error': 'Gagal menemukan file yang terdownload.'}), 500
            
        if len(audio_files) == 1:
            file_to_send = audio_files[0]
            filename = os.path.basename(file_to_send)
            
            try:
                fsize = round(os.path.getsize(file_to_send) / (1024 * 1024), 2)
                track_title = filename.rsplit('.', 1)[0]
                log_download(session.get('username'), track_title, raw_format, fsize)
            except Exception:
                pass

            resp = send_file(file_to_send, as_attachment=True, download_name=filename)
            if hasattr(resp, 'call_on_close'):
                resp.call_on_close(lambda: shutil.rmtree(job_dir, ignore_errors=True))
            return resp

        elif len(audio_files) > 1:
            zip_filename = f"{job_id}.zip"
            zip_path = os.path.join(DOWNLOADS_DIR, zip_filename)
            shutil.make_archive(zip_path[:-4], 'zip', job_dir)
            
            try:
                zsize = round(os.path.getsize(zip_path) / (1024 * 1024), 2)
                log_download(session.get('username'), f"Playlist ({len(audio_files)} lagu)", raw_format, zsize)
            except Exception:
                pass

            resp = send_file(zip_path, as_attachment=True, download_name="soundcloud_playlist.zip")
            if hasattr(resp, 'call_on_close'):
                def cleanup_zip():
                    shutil.rmtree(job_dir, ignore_errors=True)
                    if os.path.exists(zip_path):
                        try:
                            os.remove(zip_path)
                        except Exception:
                            pass
                resp.call_on_close(cleanup_zip)
            return resp

        else:
            file_to_send = files_to_check[0]
            filename = os.path.basename(file_to_send)
            
            try:
                fsize = round(os.path.getsize(file_to_send) / (1024 * 1024), 2)
                track_title = filename.rsplit('.', 1)[0]
                log_download(session.get('username'), track_title, raw_format, fsize)
            except Exception:
                pass

            resp = send_file(file_to_send, as_attachment=True, download_name=filename)
            if hasattr(resp, 'call_on_close'):
                resp.call_on_close(lambda: shutil.rmtree(job_dir, ignore_errors=True))
            return resp

    except subprocess.TimeoutExpired:
        app.logger.warning(f"[TIMEOUT] Download timed out for user {session.get('username')}: {url}")
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({'error': 'Download timeout (proses terlalu lama). Jika ini playlist atau mix panjang (di atas 30 menit), coba pilih format MP3 128kbps atau download per lagu.'}), 500
    except Exception as e:
        import traceback
        traceback.print_exc()
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({'error': str(e)}), 500
    finally:
        active_downloads.discard(user_id)
        last_download_time[user_id] = time.time()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5008))
    app.run(host='0.0.0.0', debug=False, port=port, threaded=True)

