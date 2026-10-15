#!/usr/bin/env python3
"""МУКР Кофе — бонусные карты учеников.

Один файл, только стандартная библиотека и SQLite.
Сервер отдаёт API (/api/...) и сами страницы — так сайт работает и без GitHub Pages.

    python3 server.py                          запустить (HOST, PORT из окружения)
    python3 server.py --staff barista "Айгерим" [пароль]   завести бариста
    python3 server.py --reset <телефон|логин> [пароль]      сменить пароль
"""
import hashlib, hmac, json, os, re, secrets, sqlite3, sys, threading, time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DB_PATH", ROOT / "data" / "coffee.db"))
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8036"))
ORIGINS = {o.strip() for o in os.environ.get("ALLOW_ORIGINS", "https://seinokojii.github.io").split(",") if o.strip()}

# Правила кофейни. Меняются здесь.
POINTS_PER_CUP = 10       # за любой стакан
FREE_COST = 60            # баллов за бесплатный кофе
PRICES = {"s": 140, "l": 170}
UNDO_WINDOW = 10 * 60     # бариста может отменить ошибочную операцию 10 минут
SESSION_TTL = 180 * 24 * 3600
TZ = timezone(timedelta(hours=6))  # Бишкек

STATIC = {"index.html", "app.css", "app.js", "icon.svg", "manifest.webmanifest",
          "vendor/qrcode.min.js", "vendor/jsQR.min.js"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
         ".webmanifest": "application/manifest+json"}
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # без 0/O, 1/I/L

write_lock = threading.Lock()
fails = {}  # ip -> [метки неудачных входов]


def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY, role TEXT NOT NULL DEFAULT 'student',
          login TEXT UNIQUE NOT NULL, name TEXT NOT NULL, grp TEXT NOT NULL DEFAULT '',
          pass TEXT NOT NULL, code TEXT UNIQUE NOT NULL,
          points INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(
          token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS ops(
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          staff_id INTEGER NOT NULL REFERENCES users(id),
          kind TEXT NOT NULL, size TEXT NOT NULL, delta INTEGER NOT NULL,
          ts INTEGER NOT NULL, undone INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS ops_user ON ops(user_id, ts);
        CREATE INDEX IF NOT EXISTS ops_ts ON ops(ts);
        """)


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()
    return f"{salt}${h}"


def check_pw(pw, stored):
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_pw(pw, salt), stored)


def norm_login(s):
    """Телефон → 996XXXXXXXXX; логин бариста — как есть, в нижнем регистре."""
    s = (s or "").strip()
    if re.search(r"[A-Za-zА-Яа-я]", s):
        return s.lower()
    d = re.sub(r"\D", "", s)
    if len(d) == 10 and d.startswith("0"):
        d = "996" + d[1:]
    elif len(d) == 9:
        d = "996" + d
    return d


def new_code(con):
    while True:
        c = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        if not con.execute("SELECT 1 FROM users WHERE code=?", (c,)).fetchone():
            return c


def public_user(u):
    return {"name": u["name"], "group": u["grp"], "code": u["code"], "points": u["points"],
            "role": u["role"], "login": u["login"]}


def op_row(o):
    return {"id": o["id"], "kind": o["kind"], "size": o["size"], "delta": o["delta"],
            "ts": o["ts"], "undone": bool(o["undone"])}


class ApiError(Exception):
    def __init__(self, status, msg):
        self.status, self.msg = status, msg


class Handler(BaseHTTPRequestHandler):
    server_version = "mukr-coffee"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.client_ip(), fmt % args))

    def client_ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    # ---------- ответы ----------
    def cors(self):
        o = self.headers.get("Origin")
        if o and o in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Max-Age", "86400")

    def send_json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self.api("GET")
        path = self.path.split("?", 1)[0].lstrip("/") or "index.html"
        if path not in STATIC:
            path = "index.html"
        f = ROOT / path
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(f.suffix, "application/octet-stream"))
        self.send_header("Cache-Control", "no-cache" if path == "index.html" else "public, max-age=300")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self.api("POST")
        self.send_json(404, {"error": "Нет такой страницы"})

    # ---------- API ----------
    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 10_000:
            raise ApiError(413, "Слишком большой запрос")
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise ApiError(400, "Неверный формат запроса")

    def auth(self, con, staff=False):
        h = self.headers.get("Authorization", "")
        tok = h[7:] if h.startswith("Bearer ") else ""
        u = con.execute("""SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
                           WHERE s.token=? AND s.created>?""",
                        (tok, int(time.time()) - SESSION_TTL)).fetchone() if tok else None
        if not u:
            raise ApiError(401, "Войдите заново")
        if staff and u["role"] not in ("staff", "admin"):
            raise ApiError(403, "Это действие доступно только бариста")
        return u

    def api(self, method):
        route = self.path.split("?", 1)[0][len("/api/"):].strip("/")
        try:
            con = db()
            try:
                res = self.route(con, method, route)
            finally:
                con.close()
            self.send_json(200, res)
        except ApiError as e:
            self.send_json(e.status, {"error": e.msg})
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"ERROR {method} {route}: {e!r}\n")
            self.send_json(500, {"error": "Сбой на сервере. Попробуйте ещё раз"})

    def route(self, con, m, r):
        parts = r.split("/")
        if m == "GET" and r == "rules":
            return {"perCup": POINTS_PER_CUP, "freeCost": FREE_COST, "prices": PRICES}
        if m == "POST" and r == "register":
            return self.register(con, self.body())
        if m == "POST" and r == "login":
            return self.login(con, self.body())
        if m == "POST" and r == "logout":
            h = self.headers.get("Authorization", "")
            with write_lock, con:
                con.execute("DELETE FROM sessions WHERE token=?", (h[7:],))
            return {"ok": True}
        if m == "GET" and r == "me":
            u = self.auth(con)
            ops = con.execute("SELECT * FROM ops WHERE user_id=? AND undone=0 ORDER BY ts DESC LIMIT 30",
                              (u["id"],)).fetchall()
            cups = con.execute("SELECT COUNT(*) FROM ops WHERE user_id=? AND kind='add' AND undone=0",
                               (u["id"],)).fetchone()[0]
            free = con.execute("SELECT COUNT(*) FROM ops WHERE user_id=? AND kind='redeem' AND undone=0",
                               (u["id"],)).fetchone()[0]
            return {"user": public_user(u), "history": [op_row(o) for o in ops],
                    "cups": cups, "free": free}
        if m == "GET" and len(parts) == 2 and parts[0] == "card":
            self.auth(con, staff=True)
            return {"card": public_card(self.card(con, parts[1]))}
        if m == "POST" and len(parts) == 3 and parts[0] == "card" and parts[2] in ("add", "redeem"):
            staff = self.auth(con, staff=True)
            return self.change(con, staff, parts[1], parts[2], self.body())
        if m == "POST" and len(parts) == 3 and parts[0] == "op" and parts[2] == "undo":
            staff = self.auth(con, staff=True)
            return self.undo(con, staff, parts[1])
        if m == "GET" and r == "today":
            self.auth(con, staff=True)
            return self.today(con)
        raise ApiError(404, "Нет такого адреса")

    def card(self, con, code):
        code = re.sub(r"[^A-Za-z0-9]", "", code).upper()
        u = con.execute("SELECT * FROM users WHERE code=? AND role='student'", (code,)).fetchone()
        if not u:
            raise ApiError(404, "Карта не найдена. Проверьте код")
        return u

    def register(self, con, b):
        name = " ".join(str(b.get("name", "")).split())[:60]
        grp = " ".join(str(b.get("group", "")).split())[:20]
        login = norm_login(b.get("phone"))
        pw = str(b.get("password", ""))
        if len(name) < 2:
            raise ApiError(400, "Напишите имя и фамилию")
        if not grp:
            raise ApiError(400, "Укажите группу")
        if not re.fullmatch(r"996\d{9}", login):
            raise ApiError(400, "Телефон в формате 0700 123 456 или +996 700 123 456")
        if len(pw) < 6:
            raise ApiError(400, "Пароль — не короче 6 символов")
        with write_lock, con:
            if con.execute("SELECT 1 FROM users WHERE login=?", (login,)).fetchone():
                raise ApiError(409, "Этот телефон уже зарегистрирован. Войдите")
            cur = con.execute("INSERT INTO users(role,login,name,grp,pass,code,created) VALUES('student',?,?,?,?,?,?)",
                              (login, name, grp, hash_pw(pw), new_code(con), int(time.time())))
            return self.session(con, cur.lastrowid)

    def login(self, con, b):
        ip, now = self.client_ip(), time.time()
        recent = [t for t in fails.get(ip, []) if now - t < 600]
        if len(recent) >= 10:
            raise ApiError(429, "Слишком много попыток. Подождите 10 минут")
        login = norm_login(b.get("phone"))
        u = con.execute("SELECT * FROM users WHERE login=?", (login,)).fetchone()
        if not u or not check_pw(str(b.get("password", "")), u["pass"]):
            fails[ip] = recent + [now]
            raise ApiError(401, "Неверный телефон или пароль")
        fails.pop(ip, None)
        with write_lock, con:
            return self.session(con, u["id"])

    def session(self, con, uid):
        tok = secrets.token_urlsafe(32)
        con.execute("INSERT INTO sessions VALUES(?,?,?)", (tok, uid, int(time.time())))
        u = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return {"token": tok, "user": public_user(u)}

    def change(self, con, staff, code, kind, b):
        size = b.get("size")
        if size not in PRICES:
            raise ApiError(400, "Выберите размер: маленький или большой")
        with write_lock, con:
            u = self.card(con, code)
            if kind == "add":
                delta = POINTS_PER_CUP
            else:
                if u["points"] < FREE_COST:
                    raise ApiError(409, f"Не хватает баллов: на карте {u['points']}, нужно {FREE_COST}")
                delta = -FREE_COST
            con.execute("UPDATE users SET points=points+? WHERE id=?", (delta, u["id"]))
            cur = con.execute("INSERT INTO ops(user_id,staff_id,kind,size,delta,ts) VALUES(?,?,?,?,?,?)",
                              (u["id"], staff["id"], kind, size, delta, int(time.time())))
            u = con.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
            return {"card": public_card(u), "op": {"id": cur.lastrowid, "kind": kind, "size": size, "delta": delta}}

    def undo(self, con, staff, op_id):
        with write_lock, con:
            o = con.execute("SELECT * FROM ops WHERE id=?", (op_id,)).fetchone()
            if not o or o["undone"]:
                raise ApiError(404, "Операция уже отменена или не найдена")
            if time.time() - o["ts"] > UNDO_WINDOW and staff["role"] != "admin":
                raise ApiError(409, "Отменить можно в течение 10 минут")
            u = con.execute("SELECT * FROM users WHERE id=?", (o["user_id"],)).fetchone()
            if u["points"] - o["delta"] < 0:
                raise ApiError(409, "Баллы уже потрачены — отменить нельзя")
            con.execute("UPDATE users SET points=points-? WHERE id=?", (o["delta"], u["id"]))
            con.execute("UPDATE ops SET undone=1 WHERE id=?", (op_id,))
            u = con.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
            return {"card": public_card(u)}

    def today(self, con):
        start = int(datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        rows = con.execute("""SELECT o.*, u.name, u.grp, u.code FROM ops o JOIN users u ON u.id=o.user_id
                              WHERE o.ts>=? ORDER BY o.ts DESC LIMIT 200""", (start,)).fetchall()
        live = [r for r in rows if not r["undone"]]
        return {"ops": [dict(op_row(r), name=r["name"], group=r["grp"], code=r["code"],
                             canUndo=not r["undone"] and time.time() - r["ts"] <= UNDO_WINDOW) for r in rows],
                "sold": sum(1 for r in live if r["kind"] == "add"),
                "free": sum(1 for r in live if r["kind"] == "redeem"),
                "revenue": sum(PRICES[r["size"]] for r in live if r["kind"] == "add")}


def public_card(u):
    return {"name": u["name"], "group": u["grp"], "code": u["code"], "points": u["points"]}


def cli(args):
    import getpass
    init_db()
    if args[0] == "--staff" and len(args) >= 3:
        login, name = norm_login(args[1]), args[2]
        pw = args[3] if len(args) > 3 else getpass.getpass("Пароль: ")
        with db() as con:
            if con.execute("SELECT 1 FROM users WHERE login=?", (login,)).fetchone():
                sys.exit(f"Логин {login} уже занят")
            con.execute("INSERT INTO users(role,login,name,pass,code,created) VALUES('staff',?,?,?,?,?)",
                        (login, name, hash_pw(pw), new_code(con), int(time.time())))
        print(f"Бариста {name} — логин {login}")
    elif args[0] == "--reset" and len(args) >= 2:
        login = norm_login(args[1])
        pw = args[2] if len(args) > 2 else secrets.token_urlsafe(6)
        with db() as con:
            n = con.execute("UPDATE users SET pass=? WHERE login=?", (hash_pw(pw), login)).rowcount
            con.execute("DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE login=?)", (login,))
        print(f"Новый пароль для {login}: {pw}" if n else f"Нет пользователя {login}")
    else:
        print(__doc__)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cli(sys.argv[1:])
    else:
        init_db()
        print(f"МУКР Кофе: http://{HOST}:{PORT}", flush=True)
        ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
