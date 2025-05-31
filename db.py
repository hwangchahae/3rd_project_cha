import sqlite3

DB_PATH = 'app.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 세션 테이블
    c.execute('''CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        repo_url TEXT,
        token TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    # 채팅 기록 테이블
    c.execute('''CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        role TEXT,
        content TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    # 코드 변경 내역 테이블
    c.execute('''CREATE TABLE IF NOT EXISTS code_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        file_name TEXT,
        old_code TEXT,
        new_code TEXT,
        commit_hash TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db() 