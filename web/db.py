import os
import hashlib
from datetime import datetime, timedelta

try:
    import bcrypt
except ImportError:
    bcrypt = None

DATABASE_URL = os.environ.get('DATABASE_URL', '')
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scdl.db')

class PgDBConnection:
    def __init__(self, dsn):
        import psycopg2
        import psycopg2.extras
        # Ensure sslmode=require for cloud postgres if not specified
        if 'sslmode=' not in dsn and not dsn.startswith('postgresql://localhost'):
            separator = '&' if '?' in dsn else '?'
            dsn = f"{dsn}{separator}sslmode=require"
        self.conn = psycopg2.connect(dsn)
        self.DictCursor = psycopg2.extras.DictCursor

    def execute(self, sql, params=()):
        sql = sql.replace('?', '%s')
        cur = self.conn.cursor(cursor_factory=self.DictCursor)
        cur.execute(sql, params)
        return cur

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


class SqliteDBConnection:
    def __init__(self, path):
        import sqlite3
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


def get_db():
    if DATABASE_URL:
        try:
            return PgDBConnection(DATABASE_URL)
        except Exception as e:
            print(f"[DB] Error connecting to PostgreSQL ({e}), falling back to SQLite")
    return SqliteDBConnection(DB_PATH)


def init_db():
    """Create tables and default admin account."""
    conn = get_db()
    if DATABASE_URL:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(255) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                is_admin INTEGER DEFAULT 0,
                expires_at VARCHAR(50),
                oauth_token TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    else:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                expires_at TEXT,
                oauth_token TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    conn.commit()

    # Migrate oauth_token column if table already existed without it
    try:
        conn.execute('ALTER TABLE users ADD COLUMN oauth_token TEXT')
        conn.commit()
    except Exception:
        pass

    # Create default admin if not exists
    admin = conn.execute('SELECT * FROM users WHERE LOWER(username) = LOWER(?)', ('admin',)).fetchone()
    if not admin:
        add_user('admin', 'admin123', is_admin=True, days=99999)
        print('[DB] Default admin created: admin / admin123')

    # Create settings table
    if DATABASE_URL:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key VARCHAR(100) PRIMARY KEY,
                value TEXT
            )
        ''')
    else:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
    conn.commit()

    # Ensure default master soundcloud_oauth_token is set
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = 'soundcloud_oauth_token'").fetchone()
        if not row or not row['value']:
            if DATABASE_URL:
                conn.execute("INSERT INTO settings (key, value) VALUES ('soundcloud_oauth_token', '2-333554-1763323227-qmeHBALX3Fjd8') ON CONFLICT (key) DO NOTHING")
            else:
                conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('soundcloud_oauth_token', '2-333554-1763323227-qmeHBALX3Fjd8')")
            conn.commit()
    except Exception as e:
        print(f"[DB] Error seeding soundcloud_oauth_token: {e}")

    # Ensure existing users don't get expired by default
    try:
        conn.execute("UPDATE users SET expires_at = '2099-12-31 23:59:59' WHERE expires_at < '2090-01-01'")
        conn.commit()
    except Exception:
        pass

    conn.close()


def hash_password(password):
    """Hash a password using bcrypt if available, otherwise sha256."""
    if bcrypt:
        try:
            return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        except Exception:
            pass
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def verify_password(password, hashed):
    """Verify a password against a bcrypt hash, sha256, or plaintext."""
    if not hashed:
        return False
    # Bcrypt check
    if bcrypt and isinstance(hashed, str) and (hashed.startswith('$2b$') or hashed.startswith('$2a$') or hashed.startswith('$2y$')):
        try:
            return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
        except Exception:
            return False
    # SHA-256 check (support existing database entries)
    try:
        if hashlib.sha256(password.encode('utf-8')).hexdigest() == hashed:
            return True
    except Exception:
        pass
    # Plaintext fallback
    return password == hashed


def add_user(username, password, is_admin=False, days=30):
    """Add a new user with expiry date."""
    username = username.strip()
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
    except Exception as e:
        conn.close()
        err_msg = str(e).lower()
        if 'unique' in err_msg or 'duplicate' in err_msg or 'constraint' in err_msg:
            return False, 'Username sudah dipakai'
        return False, f'Gagal menambahkan user: {e}'


def check_user(username, password):
    """Verify login credentials using bcrypt and check expiry."""
    username = username.strip()
    conn = get_db()
    user = conn.execute(
        'SELECT * FROM users WHERE LOWER(username) = LOWER(?)',
        (username,)
    ).fetchone()
    conn.close()

    if not user:
        return None, 'Username atau password salah'

    # Verify password
    if not verify_password(password, user['password']):
        return None, 'Username atau password salah'

    # Check if expired
    exp_val = user['expires_at']
    if isinstance(exp_val, datetime):
        expires = exp_val
    else:
        try:
            expires = datetime.strptime(str(exp_val)[:19], '%Y-%m-%d %H:%M:%S')
        except Exception:
            expires = datetime.now() + timedelta(days=365)

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
        'SELECT id, username, is_admin, expires_at, created_at FROM users WHERE is_admin = 0 ORDER BY id DESC'
    ).fetchall()
    conn.close()

    result = []
    for u in users:
        user_dict = dict(u)
        exp_val = u['expires_at']
        if isinstance(exp_val, datetime):
            expires_at = exp_val
            user_dict['expires_at'] = expires_at.strftime('%Y-%m-%d %H:%M:%S')
        else:
            try:
                expires_at = datetime.strptime(str(exp_val)[:19], '%Y-%m-%d %H:%M:%S')
                user_dict['expires_at'] = str(exp_val)
            except Exception:
                expires_at = datetime.now() + timedelta(days=30)
                user_dict['expires_at'] = expires_at.strftime('%Y-%m-%d %H:%M:%S')

        created_val = u['created_at']
        if isinstance(created_val, datetime):
            user_dict['created_at'] = created_val.strftime('%Y-%m-%d %H:%M:%S')
        else:
            user_dict['created_at'] = str(created_val)

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
    conn.execute('UPDATE users SET password = ? WHERE LOWER(username) = LOWER(?)', (hash_password(new_password), 'admin'))
    conn.commit()
    conn.close()


def get_setting(key, default=None):
    """Retrieve a system setting value."""
    try:
        conn = get_db()
        row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        conn.close()
        if row and row['value']:
            return row['value'].strip()
    except Exception as e:
        print(f"[DB] Error getting setting {key}: {e}")
    return default


def set_setting(key, value):
    """Set or update a system setting value."""
    try:
        conn = get_db()
        if DATABASE_URL:
            conn.execute('''
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            ''', (key, value))
        else:
            conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[DB] Error setting {key}: {e}")
        return False
