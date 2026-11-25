import os
import re
import secrets
import sqlite3
import logging
from datetime import datetime
from functools import wraps
from pathlib import Path

import requests
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "instance")))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(DATA_DIR / "uploads")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.getenv("DATABASE_PATH", str(DATA_DIR / "lostfound.db")))

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("buro")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
PUBLIC_CONTACT = os.getenv("PUBLIC_CONTACT", "Обратитесь к администратору школы").strip()
SITE_URL = os.getenv("SITE_URL", "").rstrip("/")
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

CATEGORIES = [
    ("all", "Все находки"),
    ("clothes", "Одежда"),
    ("documents", "Документы"),
    ("electronics", "Техника"),
    ("keys", "Ключи"),
    ("accessories", "Аксессуары"),
    ("school", "Школьные вещи"),
    ("other", "Другое"),
]

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'other',
                image TEXT,
                telegram_message_id INTEGER,
                telegram_chat_id TEXT,
                created_at TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at DESC)")

def guess_category(text):
    t = (text or "").lower()
    rules = {
        "clothes": ["куртк", "шапк", "шарф", "перчат", "кофт", "свитер", "футбол", "брюк", "обув", "кроссов", "форма"],
        "documents": ["паспорт", "пропуск", "удостовер", "документ", "карточк", "снилс", "полис"],
        "electronics": ["телефон", "наушник", "заряд", "флешк", "часы", "планшет", "мыш", "клавиатур"],
        "keys": ["ключ", "брелок"],
        "accessories": ["кошел", "сумк", "рюкзак", "зонт", "очки", "украш", "цепоч", "кольцо"],
        "school": ["пенал", "тетрад", "учебник", "дневник", "ручк", "ластик", "линейк", "альбом"]
    }
    for category, words in rules.items():
        if any(word in t for word in words):
            return category
    return "other"

def telegram_file(file_id):
    if not BOT_TOKEN:
        return None
    try:
        meta = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": file_id}, timeout=15
        )
        meta.raise_for_status()
        data = meta.json()
        if not data.get("ok"):
            return None
        file_path = data["result"]["file_path"]
        ext = Path(file_path).suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
            ext = ".jpg"
        content = requests.get(
            f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}", timeout=30
        )
        content.raise_for_status()
        name = f"tg_{secrets.token_hex(12)}{ext}"
        (UPLOAD_DIR / name).write_bytes(content.content)
        return name
    except Exception:
        log.exception("Не удалось загрузить фото из Telegram")
        return None

def save_telegram_message(message):
    # Игнорируем служебные сообщения, команды и сообщения без текста/фото.
    text = (message.get("caption") or message.get("text") or "").strip()
    if text.startswith("/") and not message.get("photo"):
        return
    photo = message.get("photo") or []
    image_name = telegram_file(photo[-1]["file_id"]) if photo else None
    if not text and not image_name:
        return
    clean = re.sub(r"\s+", " ", text).strip()
    title = clean.split(".")[0][:72] if clean else "Найденная вещь"
    if not title:
        title = "Найденная вещь"
    created = datetime.fromtimestamp(message.get("date", int(datetime.now().timestamp()))).isoformat(timespec="seconds")
    with db() as con:
        # Telegram retries webhook deliveries; do not create duplicates.
        exists = con.execute(
            "SELECT id FROM items WHERE telegram_chat_id=? AND telegram_message_id=?",
            (str(message.get("chat", {}).get("id", "")), message.get("message_id"))
        ).fetchone()
        if exists:
            return
        con.execute("""
            INSERT INTO items(title, description, category, image, telegram_message_id,
                              telegram_chat_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            title, clean or "Информация о находке уточняется.",
            guess_category(clean), image_name, message.get("message_id"),
            str(message.get("chat", {}).get("id", "")), created
        ))

@app.route("/")
def index():
    with db() as con:
        rows = con.execute("SELECT * FROM items ORDER BY created_at DESC").fetchall()
    items = [dict(row) for row in rows]
    return render_template("index.html", items=items, categories=CATEGORIES, contact=PUBLIC_CONTACT)

@app.route("/api/items")
def api_items():
    with db() as con:
        rows = con.execute("SELECT * FROM items ORDER BY created_at DESC").fetchall()
    return jsonify([dict(row) for row in rows])

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    # Only serve files from the dedicated upload directory.
    return app.send_static_file(filename) if False else __send_upload(filename)

def __send_upload(filename):
    from flask import send_from_directory
    return send_from_directory(UPLOAD_DIR, filename)

@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    if not WEBHOOK_SECRET:
        abort(503)
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secrets.compare_digest(supplied, WEBHOOK_SECRET):
        abort(403)
    update = request.get_json(silent=True) or {}
    message = update.get("message") or update.get("channel_post")
    if message:
        save_telegram_message(message)
    return jsonify(ok=True)

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if not ADMIN_PASSWORD:
        return render_template("admin_unconfigured.html"), 503
    if request.method == "POST":
        password = request.form.get("password", "")
        if secrets.compare_digest(password, ADMIN_PASSWORD):
            session["admin"] = True
            return redirect(url_for("admin_panel"))
        flash("Неверный пароль. Попробуйте ещё раз.", "error")
    if session.get("admin"):
        return redirect(url_for("admin_panel"))
    return render_template("admin_login.html")

@app.route("/admin/panel")
@admin_required
def admin_panel():
    with db() as con:
        rows = con.execute("SELECT * FROM items ORDER BY created_at DESC").fetchall()
    return render_template("admin.html", items=[dict(r) for r in rows])

@app.post("/admin/item/<int:item_id>/toggle")
@admin_required
def toggle_item(item_id):
    with db() as con:
        row = con.execute("SELECT resolved FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            abort(404)
        con.execute("UPDATE items SET resolved=? WHERE id=?", (0 if row["resolved"] else 1, item_id))
    return redirect(url_for("admin_panel"))

@app.post("/admin/item/<int:item_id>/delete")
@admin_required
def delete_item(item_id):
    with db() as con:
        row = con.execute("SELECT image FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            abort(404)
        con.execute("DELETE FROM items WHERE id=?", (item_id,))
    if row["image"]:
        try:
            (UPLOAD_DIR / row["image"]).unlink(missing_ok=True)
        except OSError:
            log.warning("Не удалось удалить файл изображения")
    flash("Публикация удалена.", "success")
    return redirect(url_for("admin_panel"))

@app.route("/admin/logout", methods=["POST"])
@admin_required
def admin_logout():
    session.clear()
    return redirect(url_for("index"))

@app.get("/health")
def health():
    return jsonify(status="ok")

@app.get("/setup-webhook")
def setup_webhook():
    # Protected operational endpoint; call manually only once after deployment.
    setup_key = os.getenv("SETUP_KEY", "")
    if not setup_key or request.args.get("key", "") != setup_key:
        abort(403)
    if not BOT_TOKEN or not WEBHOOK_SECRET or not SITE_URL:
        return jsonify(error="Укажите TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET и SITE_URL"), 400
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
        json={
            "url": f"{SITE_URL}/telegram/webhook",
            "secret_token": WEBHOOK_SECRET,
            "allowed_updates": ["message", "channel_post"]
        }, timeout=20
    )
    return jsonify(response.json()), response.status_code

@app.errorhandler(413)
def too_large(_):
    return "Файл слишком большой. Максимальный размер — 12 МБ.", 413

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
