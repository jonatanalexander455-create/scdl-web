import sqlite3
import os
import bcrypt
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scdl.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Create tables and default admin account."""
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_admin INTEGER DEFAULT 0,
            expires_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    
    # Create default admin if not exists
    admin = conn.execute('SELECT * FROM users WHERE username = ?', ('admin',)).fetchone()
    if not admin:
        add_user('admin', 'admin123', is_admin=True, days=99999)
        print('[DB] Default admin created: admin / admin123')
    
    conn.close()

def hash_password(password):
    """Hash a password using bcrypt with auto-generated salt."""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password, hashed):
    """Verify a password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False

def add_user(username, password, is_admin=False, days=30):
    """Add a new user with expiry date."""
    conn = get_db()
    expires_at = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    try:
        conn.execute(
            'INSERT INTO users (username, password, is_admin, expires_at) VALUES (?, ?, ?, ?)',
            (username, hash_password(password), 1 if is_admin else 0, expires_at)
        )
        conn.commit()
        conn.close()
        return True, 'User berhasil ditambahkan'
    except sqlite3.IntegrityError:
        conn.close()
        return False, 'Username sudah dipakai'

def check_user(username, password):
    """Verify login credentials using bcrypt and check expiry."""
    conn = get_db()
    user = conn.execute(
        'SELECT * FROM users WHERE username = ?',
        (username,)
    ).fetchone()
    conn.close()
    
    if not user:
        return None, 'Username atau password salah'
    
    # Verify password with bcrypt
    if not verify_password(password, user['password']):
        return None, 'Username atau password salah'
    
    # Check if expired
    expires = datetime.strptime(user['expires_at'], '%Y-%m-%d %H:%M:%S')
    if datetime.now() > expires and not user['is_admin']:
        return None, 'Langganan sudah habis'
    
    return dict(user), None

def reset_user_password(user_id, new_password):
    """Reset the password for a specific user ID."""
    conn = get_db()
    try:
        conn.execute(
            'UPDATE users SET password = ? WHERE id = ?',
            (hash_password(new_password), user_id)
        )
        conn.commit()
        return True, 'Kata sandi berhasil direset'
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

def get_all_users():
    """Get all non-admin users."""
    conn = get_db()
    users = conn.execute(
        'SELECT id, username, is_admin, expires_at, created_at FROM users WHERE is_admin = 0 ORDER BY created_at DESC'
    ).fetchall()
    conn.close()
    
    result = []
    for u in users:
        user_dict = dict(u)
        expires_at = datetime.strptime(u['expires_at'], '%Y-%m-%d %H:%M:%S')
        user_dict['is_active'] = datetime.now() < expires_at
        user_dict['days_left'] = max(0, (expires_at - datetime.now()).days)
        result.append(user_dict)
    return result

def delete_user(user_id):
    """Delete a user by ID."""
    conn = get_db()
    conn.execute('DELETE FROM users WHERE id = ? AND is_admin = 0', (user_id,))
    conn.commit()
    conn.close()

def extend_user(user_id, days=30):
    """Extend a user's subscription by X days from now."""
    conn = get_db()
    new_expiry = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('UPDATE users SET expires_at = ? WHERE id = ? AND is_admin = 0', (new_expiry, user_id))
    conn.commit()
    conn.close()

def change_admin_password(new_password):
    """Change admin password."""
    conn = get_db()
    conn.execute('UPDATE users SET password = ? WHERE username = ?', (hash_password(new_password), 'admin'))
    conn.commit()
    conn.close()
