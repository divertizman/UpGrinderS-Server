import os, sqlite3, time
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DB_PATH = os.getenv('DATABASE_PATH', 'upgrinders_scores.db')
PERIOD_SECONDS = 12 * 60 * 60
app = FastAPI(title='UpGrinderS Shared Tetris Leaderboard', version='1.0.0')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['GET','POST'], allow_headers=['*'])

@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        con.execute('''CREATE TABLE IF NOT EXISTS scores (
            wallet TEXT PRIMARY KEY,
            points INTEGER NOT NULL DEFAULT 0,
            period_id INTEGER NOT NULL,
            last_win INTEGER NOT NULL DEFAULT 0
        )''')
        con.commit()
        yield con
    finally:
        con.close()

def current_period(now=None):
    now = int(now or time.time())
    return (now // PERIOD_SECONDS) * PERIOD_SECONDS

class WinRequest(BaseModel):
    wallet: str = Field(min_length=32, max_length=64, pattern=r'^[1-9A-HJ-NP-Za-km-z]+$')
    # Optional unique round ID to prevent duplicate requests for the same game.
    round_id: str = Field(min_length=1, max_length=100)

@app.get('/health')
def health():
    return {'ok': True, 'service': 'UpGrinderS leaderboard'}

@app.post('/win')
def record_win(payload: WinRequest):
    now = int(time.time())
    period = current_period(now)
    with db() as con:
        # Reset stale rows on first request after a 12-hour boundary.
        con.execute('DELETE FROM scores WHERE period_id != ?', (period,))
        # Idempotency table prevents repeated same round_id/wallet submissions.
        con.execute('''CREATE TABLE IF NOT EXISTS awarded_rounds (
            wallet TEXT NOT NULL, round_id TEXT NOT NULL, period_id INTEGER NOT NULL,
            PRIMARY KEY(wallet, round_id, period_id)
        )''')
        try:
            con.execute('INSERT INTO awarded_rounds(wallet,round_id,period_id) VALUES(?,?,?)',
                       (payload.wallet, payload.round_id, period))
        except sqlite3.IntegrityError:
            row = con.execute('SELECT points FROM scores WHERE wallet=? AND period_id=?',
                             (payload.wallet, period)).fetchone()
            return {'ok': True, 'duplicate': True, 'points': row['points'] if row else 0,
                    'period_resets_at': period + PERIOD_SECONDS}
        con.execute('''INSERT INTO scores(wallet,points,period_id,last_win) VALUES(?,1,?,?)
                      ON CONFLICT(wallet) DO UPDATE SET
                      points=CASE WHEN scores.period_id=? THEN scores.points+1 ELSE 1 END,
                      period_id=excluded.period_id, last_win=excluded.last_win''',
                   (payload.wallet, period, now, period))
        row = con.execute('SELECT points FROM scores WHERE wallet=?', (payload.wallet,)).fetchone()
        con.commit()
        return {'ok': True, 'duplicate': False, 'points': row['points'],
                'period_resets_at': period + PERIOD_SECONDS}

@app.get('/leaderboard')
def leaderboard():
    now = int(time.time())
    period = current_period(now)
    with db() as con:
        con.execute('DELETE FROM scores WHERE period_id != ?', (period,))
        con.execute('DELETE FROM awarded_rounds WHERE period_id != ?', (period,))
        rows = con.execute('SELECT wallet,points,last_win FROM scores WHERE period_id=? ORDER BY points DESC,last_win ASC',
                          (period,)).fetchall()
        con.commit()
    return {'period_started_at': period, 'period_resets_at': period + PERIOD_SECONDS,
            'seconds_until_reset': max(0, period + PERIOD_SECONDS - now),
            'entries': [{'rank': i+1, 'wallet': r['wallet'], 'points': r['points'],
                        'last_win': r['last_win']} for i,r in enumerate(rows)]}
