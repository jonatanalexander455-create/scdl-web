from flask import Flask, render_template, request, jsonify, send_file, after_this_request, session, redirect, url_for
from functools import wraps
from datetime import timedelta
import subprocess
import os
import tempfile
import shutil
import uuid
import time
import secrets
import re
import requests as req
from db import init_db, check_user, get_all_users, add_user, delete_user, extend_user, reset_user_password

app = Flask(__name__)

# Basic paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(BASE_DIR, 'downloads')
# ProxyFix for Render reverse proxy
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Persistent Secret Key across deploys
SECRET_KEY = os.environ.get('SECRET_KEY', 'scdl-persistent-secret-key-2026-prod-stable')
app.secret_key = SECRET_KEY.encode('utf-8') if isinstance(SECRET_KEY, str) else SECRET_KEY

# Ensure downloads directory exists
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Initialize database
init_db()

# ===================== SECURITY: SESSION TIMEOUT (#4) =====================
app.permanent_session_lifetime = timedelta(days=7)

@app.before_request
def make_session_permanent():
    session.permanent = True

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
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session or not session.get('is_admin'):
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
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_admin'] = bool(user['is_admin'])
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
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_admin'] = True
            return redirect(url_for('admin'))
        else:
            record_failed_login(ip)
            return render_template('admin_login.html', error='Username atau password admin salah')
    
    return render_template('admin_login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ===================== ADMIN ROUTES =====================

@app.route('/kelola-scdl')
@admin_required
def admin():
    users = get_all_users()
    disk = get_disk_info()
    cookies_path = os.path.join(os.path.dirname(__file__), 'cookies.txt')
    has_cookies = os.path.exists(cookies_path) and os.path.getsize(cookies_path) > 10
    cookies_content = ''
    if has_cookies:
        try:
            with open(cookies_path, 'r', encoding='utf-8') as f:
                cookies_content = f.read()
        except Exception:
            pass
    return render_template('admin.html', users=users, disk=disk, has_cookies=has_cookies, cookies_content=cookies_content)

@app.route('/kelola-scdl/add', methods=['POST'])
@admin_required
@csrf_required
def admin_add():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    days = int(request.form.get('days', 30))
    
    success, msg = add_user(username, password, days=days)
    users = get_all_users()
    disk = get_disk_info()
    
    if success:
        return render_template('admin.html', users=users, disk=disk, message=f'✅ User "{username}" berhasil ditambahkan ({days} hari)')
    else:
        err = f'User "{username}" sudah ada! Klik tombol "Reset Pwd" di daftar pelanggan di bawah jika ingin mengubah password.' if 'sudah dipakai' in msg else msg
        return render_template('admin.html', users=users, disk=disk, error=f'❌ {err}')

@app.route('/kelola-scdl/delete', methods=['POST'])
@admin_required
@csrf_required
def admin_delete():
    user_id = int(request.form.get('user_id'))
    delete_user(user_id)
    users = get_all_users()
    disk = get_disk_info()
    return render_template('admin.html', users=users, disk=disk, message='✅ User berhasil dihapus')

@app.route('/kelola-scdl/extend', methods=['POST'])
@admin_required
@csrf_required
def admin_extend():
    user_id = int(request.form.get('user_id'))
    days = int(request.form.get('days', 30))
    extend_user(user_id, days)
    users = get_all_users()
    disk = get_disk_info()
    return render_template('admin.html', users=users, disk=disk, message=f'✅ Langganan diperpanjang {days} hari')

@app.route('/kelola-scdl/reset-password', methods=['POST'])
@admin_required
@csrf_required
def admin_reset_password():
    user_id = int(request.form.get('user_id'))
    new_password = request.form.get('new_password', '').strip()
    
    if len(new_password) < 4:
        users = get_all_users()
        disk = get_disk_info()
        return render_template('admin.html', users=users, disk=disk, error='❌ Password minimal 4 karakter')
        
    success, msg = reset_user_password(user_id, new_password)
    users = get_all_users()
    disk = get_disk_info()
    
    if success:
        return render_template('admin.html', users=users, disk=disk, message='✅ ' + msg)
    else:
        return render_template('admin.html', users=users, disk=disk, error='❌ ' + msg)

@app.route('/kelola-scdl/cookies', methods=['POST'])
@admin_required
@csrf_required
def admin_save_cookies():
    content = request.form.get('cookies_content', '').strip()
    cookies_path = os.path.join(os.path.dirname(__file__), 'cookies.txt')
    users = get_all_users()
    disk = get_disk_info()
    if content:
        with open(cookies_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return render_template('admin.html', users=users, disk=disk, has_cookies=True, message='✅ Cookies YouTube berhasil disimpan! Semua lagu DRM sekarang bebas bot-check.')
    else:
        if os.path.exists(cookies_path):
            os.remove(cookies_path)
        return render_template('admin.html', users=users, disk=disk, has_cookies=False, message='✅ Cookies YouTube telah dikosongkan.')

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
    
    users = get_all_users()
    disk = get_disk_info()
    return render_template('admin.html', users=users, disk=disk,
                           message=f'🧹 Berhasil membersihkan {cleaned} item dari storage')

# ===================== MAIN ROUTES =====================

@app.route('/')
@login_required
def index():
    user_id = session.get('user_id')
    saved_token = ''
    try:
        from db import get_db
        conn = get_db()
        user = conn.execute('SELECT oauth_token FROM users WHERE id = ?', (user_id,)).fetchone()
        conn.close()
        if user and user['oauth_token']:
            saved_token = user['oauth_token']
    except Exception as e:
        app.logger.error(f"Error loading oauth_token: {e}")
    return render_template('index.html', saved_token=saved_token)


@app.route('/api/save_token', methods=['POST'])
@login_required
def save_token():
    data = request.json
    auth_token = data.get('auth_token', '').strip()
    user_id = session.get('user_id')
    
    if not auth_token:
        return jsonify({'error': 'Token tidak boleh kosong'}), 400
        
    try:
        from db import get_db
        conn = get_db()
        conn.execute('UPDATE users SET oauth_token = ? WHERE id = ?', (auth_token, user_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'Token berhasil disimpan ke akun kamu'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/download', methods=['POST'])
@login_required
def download():
    data = request.json
    url = data.get('url')
    auth_token = data.get('auth_token')
    audio_format = data.get('format', 'mp3')
    
    # Validate format
    allowed_formats = ['mp3', 'flac', 'wav', 'aac', 'ogg', 'opus', 'm4a']
    if audio_format not in allowed_formats:
        audio_format = 'mp3'
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
        
    # Resolve shortlink if on.soundcloud.com
    if 'on.soundcloud.com' in url:
        try:
            import requests as req
            resolved = req.get(url, allow_redirects=True, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            if resolved.url and 'soundcloud.com' in resolved.url:
                url = resolved.url
        except Exception as e:
            app.logger.warning(f"Failed to resolve shortlink: {e}")

    # Clean and resolve auth_token
    clean_token = auth_token.strip() if auth_token else ''
    if not clean_token:
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

    # Create a unique temporary directory for this download
    job_id = str(uuid.uuid4())
    job_dir = os.path.join(DOWNLOADS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    
    try:
        # Dynamically locate yt-dlp executable
        YT_DLP = shutil.which('yt-dlp') or '/Users/johoktan11/Library/Python/3.9/bin/yt-dlp' or 'yt-dlp'
        cmd = [
            YT_DLP,
            '-x', '--audio-format', audio_format,
            '--embed-thumbnail',
            '--embed-metadata',
            '--convert-thumbnails', 'jpg',
            '-o', os.path.join(job_dir, '%(title)s.%(ext)s'),
        ]
        
        if clean_token:
            cmd.extend([
                '--username', 'oauth',
                '--password', clean_token,
                '--add-header', f'Authorization: OAuth {clean_token}',
            ])

        cmd.append(url)

            
        # Run the command with timeout
        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
        
        if process.returncode != 0:
            err_output = process.stderr or process.stdout or ''
            app.logger.info(f"Direct SoundCloud download returned non-zero, activating Smart Hybrid Mirror. Stderr: {err_output[:200]}")
            
            fallback_success = False
            meta = None
            
            # Step 1: Fetch official metadata & artwork from SoundCloud API
            try:
                headers = {'User-Agent': 'Mozilla/5.0'}
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
                        sr = req.get(f'https://api-v2.soundcloud.com/search/tracks?q={search_query}&client_id=iZIs9mchVcX5lhVRKiQWVU8hlbxSPerform', headers=headers, timeout=10)
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
                
                def find_valid_audio(d):
                    valid_exts = ('.mp3', '.m4a', '.opus', '.flac', '.wav', '.aac', '.ogg')
                    return [
                        os.path.join(d, f) for f in os.listdir(d)
                        if any(f.lower().endswith(ext) for ext in valid_exts)
                        and not f.endswith('.part') and not f.endswith('.ytdl')
                        and os.path.isfile(os.path.join(d, f))
                        and os.path.getsize(os.path.join(d, f)) > 10000
                    ]
                
                # Smart Duration Matching: prioritize exact duration matching (+-6s), then relaxed (+-15s)
                match_filters = []
                if duration_sec > 10:
                    match_filters.append(f"duration >= {max(1, duration_sec - 6)} & duration <= {duration_sec + 6}")
                    match_filters.append(f"duration >= {max(1, duration_sec - 15)} & duration <= {duration_sec + 15}")
                match_filters.append(None)
                
                # Check for optional cookies file
                cookies_file = None
                for cand_path in [
                    os.path.join(os.path.dirname(__file__), 'cookies.txt'),
                    os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cookies.txt'),
                    '/app/cookies.txt'
                ]:
                    if os.path.exists(cand_path) and os.path.getsize(cand_path) > 10:
                        cookies_file = cand_path
                        break

                fallback_proc = None
                for mf in match_filters:
                    for query in [f"ytsearch5:{clean_search}", f"ytsearch5:{clean_search} audio"]:
                        yt_cmd = [
                            YT_DLP,
                            '--no-playlist',
                            '--max-downloads', '1',
                            '--ignore-errors',
                            '--no-abort-on-error',
                            '--extractor-args', 'youtube:player_client=android',
                            '-f', '18/bestaudio/best',
                            '-x', '--audio-format', audio_format,
                            '--embed-metadata',
                            '-o', os.path.join(job_dir, '%(title)s.%(ext)s'),
                        ]
                        if cookies_file:
                            yt_cmd.extend(['--cookies', cookies_file])
                        if mf:
                            yt_cmd.extend(['--match-filter', mf])
                        yt_cmd.append(query)
                        
                        fallback_proc = subprocess.run(yt_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
                        if find_valid_audio(job_dir):
                            break
                    if find_valid_audio(job_dir):
                        break
                
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
            
        # Helper to find all files
        def get_all_files(directory):
            file_paths = []
            for root, directories, files in os.walk(directory):
                for filename in files:
                    filepath = os.path.join(root, filename)
                    file_paths.append(filepath)
            return file_paths
            
        all_files = get_all_files(job_dir)
        
        if not all_files:
            shutil.rmtree(job_dir, ignore_errors=True)
            return jsonify({'error': 'Gagal menemukan file yang terdownload.'}), 500
            
        if len(all_files) == 1:
            file_to_send = all_files[0]
            filename = os.path.basename(file_to_send)
            
            @after_this_request
            def remove_file(response):
                try:
                    shutil.rmtree(job_dir, ignore_errors=True)
                except Exception as e:
                    app.logger.error(f"Error removing file: {e}")
                return response
                
            return send_file(file_to_send, as_attachment=True, download_name=filename)
        else:
            zip_filename = f"{job_id}.zip"
            zip_path = os.path.join(DOWNLOADS_DIR, zip_filename)
            shutil.make_archive(zip_path[:-4], 'zip', job_dir)
            
            @after_this_request
            def remove_files(response):
                try:
                    shutil.rmtree(job_dir, ignore_errors=True)
                    if os.path.exists(zip_path):
                        os.remove(zip_path)
                except Exception as e:
                    app.logger.error(f"Error removing files: {e}")
                return response
                
            return send_file(zip_path, as_attachment=True, download_name="soundcloud_playlist.zip")

    except subprocess.TimeoutExpired:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({'error': 'Download timeout setelah 2 menit. Coba lagi.'}), 500
    except Exception as e:
        import traceback
        traceback.print_exc()
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5008))
    app.run(host='0.0.0.0', debug=False, port=port, threaded=True)

