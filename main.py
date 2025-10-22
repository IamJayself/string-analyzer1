# main.py
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import hashlib, sqlite3, json, os, re
from datetime import datetime, timezone

app = FastAPI(title="String Analyzer - Stage 1")

DB_PATH = os.environ.get("DB_PATH", "strings.db")

# ---------- DB helpers ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS strings (
        id TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        properties TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)
    conn.commit()
    conn.close()

def get_conn():
    # short-lived connection per request
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

init_db()

# ---------- analysis helpers ----------
def sha256_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def normalize_for_palindrome(s: str) -> str:
    # case-insensitive, ignore whitespace
    return re.sub(r"\s+", "", s).lower()

def is_palindrome_value(s: str) -> bool:
    norm = normalize_for_palindrome(s)
    return norm == norm[::-1]

def character_frequency_map(s: str) -> Dict[str, int]:
    freq: Dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    return freq

def analyze_string(value: str) -> Dict[str, Any]:
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    v = value
    sha = sha256_hash(v)
    props = {
        "length": len(v),
        "is_palindrome": is_palindrome_value(v),
        "unique_characters": len(set(v)),
        "word_count": 0 if v.strip() == "" else len(v.split()),
        "sha256_hash": sha,
        "character_frequency_map": character_frequency_map(v)
    }
    return props

# ---------- models ----------
class CreateReq(BaseModel):
    value: str

# ---------- CRUD endpoints ----------
@app.post("/strings", status_code=201)
def create_string(req: CreateReq):
    # Pydantic will enforce type; missing value -> 422
    value = req.value
    if value is None:
        # defensive, but Pydantic should handle this
        raise HTTPException(status_code=422, detail="Missing 'value' field")
    props = analyze_string(value)
    sid = props["sha256_hash"]
    created_at = datetime.now(timezone.utc).isoformat()

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM strings WHERE id = ?", (sid,))
        if cur.fetchone():
            raise HTTPException(status_code=409, detail="String already exists")
        cur.execute(
            "INSERT INTO strings (id, value, properties, created_at) VALUES (?, ?, ?, ?)",
            (sid, value, json.dumps(props), created_at)
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "id": sid,
        "value": value,
        "properties": props,
        "created_at": created_at
    }

@app.get("/strings/{string_value}")
def get_string(string_value: str):
    # Look up by exact value's SHA256
    sid = sha256_hash(string_value)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, value, properties, created_at FROM strings WHERE id = ?", (sid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="String does not exist")
        return {
            "id": row["id"],
            "value": row["value"],
            "properties": json.loads(row["properties"]),
            "created_at": row["created_at"]
        }
    finally:
        conn.close()

# ---------- filtering logic ----------
def match_filters(props: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    # props come from stored analysis
    # is_palindrome
    if "is_palindrome" in filters:
        if props.get("is_palindrome") != filters["is_palindrome"]:
            return False
    if "min_length" in filters:
        if props.get("length", 0) < filters["min_length"]:
            return False
    if "max_length" in filters:
        if props.get("length", 0) > filters["max_length"]:
            return False
    if "word_count" in filters:
        if props.get("word_count") != filters["word_count"]:
            return False
    if "contains_character" in filters:
        ch = filters["contains_character"]
        # case-insensitive search across characters in original string representation
        # keys in character_frequency_map are case-sensitive chars; we'll check both lower/upper
        freq = props.get("character_frequency_map", {})
        # Accept single-character strings only (validated earlier)
        found = False
        # check direct presence in freq keys (both cases)
        if ch in freq or ch.lower() in freq or ch.upper() in freq:
            found = True
        if not found:
            return False
    return True

@app.get("/strings")
def list_strings(
    is_palindrome: Optional[bool] = Query(None),
    min_length: Optional[int] = Query(None),
    max_length: Optional[int] = Query(None),
    word_count: Optional[int] = Query(None),
    contains_character: Optional[str] = Query(None)
):
    # Validate query parameter values
    filters: Dict[str, Any] = {}
    if is_palindrome is not None:
        if not isinstance(is_palindrome, bool):
            raise HTTPException(status_code=400, detail="is_palindrome must be boolean")
        filters["is_palindrome"] = is_palindrome
    if min_length is not None:
        if min_length < 0:
            raise HTTPException(status_code=400, detail="min_length must be >= 0")
        filters["min_length"] = min_length
    if max_length is not None:
        if max_length < 0:
            raise HTTPException(status_code=400, detail="max_length must be >= 0")
        filters["max_length"] = max_length
    if min_length is not None and max_length is not None and min_length > max_length:
        raise HTTPException(status_code=400, detail="min_length cannot be greater than max_length")
    if word_count is not None:
        if word_count < 0:
            raise HTTPException(status_code=400, detail="word_count must be >= 0")
        filters["word_count"] = word_count
    if contains_character is not None:
        if not isinstance(contains_character, str) or len(contains_character) != 1:
            raise HTTPException(status_code=400, detail="contains_character must be a single character")
        filters["contains_character"] = contains_character

    conn = get_conn()
    results: List[Dict[str, Any]] = []
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, value, properties, created_at FROM strings")
        rows = cur.fetchall()
        for row in rows:
            props = json.loads(row["properties"])
            if match_filters(props, filters):
                results.append({
                    "id": row["id"],
                    "value": row["value"],
                    "properties": props,
                    "created_at": row["created_at"]
                })
    finally:
        conn.close()

    return {"data": results, "count": len(results), "filters_applied": filters}

# ---------- natural language filtering ----------
VOWELS = ["a", "e", "i", "o", "u"]

def parse_nl_query(q: str) -> Dict[str, Any]:
    q = (q or "").lower().strip()
    parsed: Dict[str, Any] = {}

    # single-word / one word
    if re.search(r"\b(single|one)[ -]?word\b", q) or re.search(r"\bon[e]?[- ]word\b", q):
        parsed["word_count"] = 1

    # palindrome mentions
    if "palindrom" in q:
        parsed["is_palindrome"] = True

    # "strings longer than N" -> min_length = N + 1
    m = re.search(r"longer than (\d+)", q)
    if m:
        n = int(m.group(1))
        parsed["min_length"] = n + 1

    # explicit "strings longer than or equal to N" or "at least N" -> min_length = N
    m2 = re.search(r"(?:at least|>=|greater than or equal to) (\d+)", q)
    if m2:
        parsed["min_length"] = int(m2.group(1))

    # "shorter than N" -> max_length = N - 1
    m3 = re.search(r"shorter than (\d+)", q)
    if m3:
        n = int(m3.group(1))
        parsed["max_length"] = max(0, n - 1)

    # "strings containing the letter z" or "contain the letter z"
    m4 = re.search(r"contain(?:ing|s)? (?:the )?letter ([a-z])", q)
    if m4:
        parsed["contains_character"] = m4.group(1)

    # "strings containing the letter z" simpler variant
    m4b = re.search(r"containing ([a-z])", q)
    if m4b and "contains_character" not in parsed:
        parsed["contains_character"] = m4b.group(1)

    # "palindromic strings that contain the first vowel"
    if "first vowel" in q:
        # heuristic: choose 'a' as the first vowel
        parsed["contains_character"] = "a"
        parsed["is_palindrome"] = parsed.get("is_palindrome", True)  # often appears with palindromic

    # "strings that contain the first vowel" without palindrome
    if re.search(r"first vowel", q) and "is_palindrome" not in parsed:
        parsed["contains_character"] = parsed.get("contains_character", "a")

    return parsed

@app.get("/strings/filter-by-natural-language")
def filter_by_nl(query: str = Query(..., min_length=1)):
    if not query or not isinstance(query, str):
        raise HTTPException(status_code=400, detail="query parameter is required")
    parsed = parse_nl_query(query)
    if not parsed:
        raise HTTPException(status_code=400, detail="Unable to parse natural language query")

    # check conflicts (e.g., min_length > max_length)
    if "min_length" in parsed and "max_length" in parsed and parsed["min_length"] > parsed["max_length"]:
        raise HTTPException(status_code=422, detail="Parsed filters conflict (min_length > max_length)")

    # Validate contains_character length if present
    if "contains_character" in parsed:
        ch = parsed["contains_character"]
        if not isinstance(ch, str) or len(ch) != 1:
            raise HTTPException(status_code=422, detail="Parsed contains_character invalid")

    conn = get_conn()
    results: List[Dict[str, Any]] = []
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, value, properties, created_at FROM strings")
        for row in cur.fetchall():
            props = json.loads(row["properties"])
            if match_filters(props, parsed):
                results.append({
                    "id": row["id"],
                    "value": row["value"],
                    "properties": props,
                    "created_at": row["created_at"]
                })
    finally:
        conn.close()

    return {
        "data": results,
        "count": len(results),
        "interpreted_query": {
            "original": query,
            "parsed_filters": parsed
        }
    }

# ---------- delete ----------
@app.delete("/strings/{string_value}", status_code=204)
def delete_string(string_value: str):
    sid = sha256_hash(string_value)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM strings WHERE id = ?", (sid,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="String does not exist")
    finally:
        conn.close()
    # 204 No Content -> empty response body
    return None
