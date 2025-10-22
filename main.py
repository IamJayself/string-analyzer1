from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import hashlib, sqlite3, json
from datetime import datetime, timezone

app = FastAPI()

DB_NAME = "strings.db"

# ---------- database setup ----------
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS strings (
        id TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        properties TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)
    conn.commit()
    conn.close()

init_db()

# ---------- helper functions ----------
def analyze_string(value: str):
    value = value.strip()
    sha256_hash = hashlib.sha256(value.encode()).hexdigest()
    is_palindrome = value.lower().replace(" ", "") == value.lower().replace(" ", "")[::-1]
    word_count = len(value.split())
    unique_characters = len(set(value))
    length = len(value)
    freq = {}
    for ch in value:
        freq[ch] = freq.get(ch, 0) + 1
    return {
        "length": length,
        "is_palindrome": is_palindrome,
        "unique_characters": unique_characters,
        "word_count": word_count,
        "sha256_hash": sha256_hash,
        "character_frequency_map": freq
    }

# ---------- request model ----------
class StringRequest(BaseModel):
    value: str

# ---------- routes ----------
@app.post("/strings", status_code=201)
def create_string(req: StringRequest):
    if not req.value:
        raise HTTPException(status_code=400, detail="Missing string value")

    props = analyze_string(req.value)
    sid = props["sha256_hash"]
    created_at = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT * FROM strings WHERE id=?", (sid,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=409, detail="String already exists")

    cur.execute(
        "INSERT INTO strings (id, value, properties, created_at) VALUES (?, ?, ?, ?)",
        (sid, req.value, json.dumps(props), created_at)
    )
    conn.commit()
    conn.close()

    return {
        "id": sid,
        "value": req.value,
        "properties": props,
        "created_at": created_at
    }

@app.get("/strings/{string_value}")
def get_string(string_value: str):
    sid = hashlib.sha256(string_value.encode()).hexdigest()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT * FROM strings WHERE id=?", (sid,))
    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="String not found")

    return {
        "id": row[0],
        "value": row[1],
        "properties": json.loads(row[2]),
        "created_at": row[3]
    }

@app.get("/")
def home():
    return {"message": "Welcome to String Analyzer API 🎯"}
