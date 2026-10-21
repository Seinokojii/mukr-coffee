#!/usr/bin/env python3
"""МУКР Кофе — бонусные карты учеников, оплата по QR, касса и кабинет администрации.

Один файл, только стандартная библиотека и SQLite.
Сервер отдаёт API (/api/...) и сами страницы — так сайт работает и без GitHub Pages.

    python3 server.py                                     запустить (HOST, PORT из окружения)
    python3 server.py --staff barista "Айгерим" [пароль]  завести бариста
    python3 server.py --admin admin "Администратор" [пароль]  завести администратора
    python3 server.py --reset <телефон|логин> [пароль]     сменить пароль

Оплата по QR (GoPay, ELQR — MBank, MegaPay, Оптима и др.):
    GOPAY_API_KEY, GOPAY_SECRET_KEY   ключи из кабинета merchant.gopay.kg (Test или Live)
    GOPAY_WEBHOOK_SECRET              секрет вебхука (Разработчику → Вебхуки), events_url = PUBLIC_URL/api/pay/gopay
    PUBLIC_URL                        адрес этого сервера, напр. https://mukrcoffee.62-113-109-193.sslip.io
    PAY_MODE=desk                     без ключей: ученик заказывает в приложении, платит на кассе; бариста
                                      сканирует его QR, отмечает «наличные» или «картой» — так засчитываются деньги и баллы
    PAY_MODE=demo                     то же, но для показа: такие продажи помечаются демо и не входят в выручку

Нагрузка и защита: пул из WORKERS потоков (по умолчанию 32) и очередь соединений на 1024 — лишние ждут,
а не обрываются; лимиты запросов на сессию и на адрес; не больше двух проверок пароля одновременно.
Каждые сутки — копия базы в data/backups (хранится 14 штук).
"""
import gzip, hashlib, hmac, ipaddress, json, os, queue, re, secrets, selectors, socket, sqlite3, sys, threading, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DB_PATH", ROOT / "data" / "coffee.db"))
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8036"))
ORIGINS = {o.strip() for o in os.environ.get("ALLOW_ORIGINS", "https://seinokojii.github.io").split(",") if o.strip()}
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

GOPAY_URL = os.environ.get("GOPAY_URL", "https://api.gopay.kg")
GOPAY_KEY = os.environ.get("GOPAY_API_KEY", "")
GOPAY_SECRET = os.environ.get("GOPAY_SECRET_KEY", "")
GOPAY_HOOK_SECRET = os.environ.get("GOPAY_WEBHOOK_SECRET", "")
PAY_MODE = "gopay" if GOPAY_KEY and GOPAY_SECRET else (os.environ.get("PAY_MODE") if os.environ.get("PAY_MODE") in ("desk", "demo") else "off")
CARD_QR = "MUKRCOFFEE:"       # личный QR ученика (тот же, что на карте)
PICKUP = "MUKRCOFFEE-ORDER:"  # QR заказа у ученика: бариста сканирует его — заказ подтверждён и выдан

# Правила кофейни по умолчанию. Администратор меняет их в кабинете (таблица settings).
DEFAULTS = {"per_cup": 10, "free_cost": 60, "price_s": 140, "price_l": 170}
SIZES = ("s", "l")
ORDER_TTL = 15 * 60       # сколько живёт неоплаченный заказ
UNDO_WINDOW = 10 * 60     # бариста может отменить ошибочную операцию 10 минут
SESSION_TTL = 180 * 24 * 3600        # ученик
STAFF_TTL = 14 * 24 * 3600           # касса и администрация входят заново раз в две недели
TZ = timezone(timedelta(hours=6))  # Бишкек

STATIC = {"index.html", "app.css", "app.js", "icon.svg", "manifest.webmanifest",
          "vendor/qrcode.min.js", "vendor/jsQR.min.js"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
         ".webmanifest": "application/manifest+json"}
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # без 0/O, 1/I/L

WORKERS = int(os.environ.get("WORKERS", "32"))
PROXIES = [ipaddress.ip_network(n) for n in os.environ.get("TRUSTED_PROXIES", "127.0.0.0/8,172.16.0.0/12").split(",")]
BACKUP_DIR = DB_PATH.parent / "backups"

write_lock = threading.Lock()
hash_slots = threading.BoundedSemaphore(2)  # PBKDF2 дорогой: не даём сотне входов разом съесть процессор
BODY_DEADLINE = 5     # тело запроса должно прийти целиком за 5 с, иначе соединение рвём


class Limiter:
    """Ведро с жетонами на ключ (сессия, адрес, логин): rate жетонов в секунду, запас burst."""

    def __init__(self, rate, burst):
        self.rate, self.burst, self.b, self.lock = rate, burst, {}, threading.Lock()

    def take(self, key, n=1):
        now = time.monotonic()
        with self.lock:
            tokens, ts = self.b.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - ts) * self.rate)
            if tokens < n:
                self.b[key] = (tokens, now)
                return False
            self.b[key] = (tokens - n, now)
            return True

    def give(self, key, n=1):
        """Вернуть жетон (вход удался — неудачной попыткой он не считается)."""
        with self.lock:
            if key in self.b:
                tokens, ts = self.b[key]
                self.b[key] = (min(self.burst, tokens + n), ts)

    def sweep(self):
        """Забыть вёдра, которые давно полные (зовётся из housekeeping, а не на каждом запросе)."""
        now = time.monotonic()
        with self.lock:
            self.b = {k: v for k, v in self.b.items() if v[0] + (now - v[1]) * self.rate < self.burst}


# Лимиты подобраны под кампус, где сотни учеников сидят за одним адресом Wi-Fi:
# запросы с сессией считаем по сессии, без сессии — по адресу, но щедро.
LIM_SESSION = Limiter(5, 30)          # приложение ученика опрашивает раз в 2–5 с — запас десятикратный
LIM_STAFF = Limiter(20, 120)          # касса
LIM_IP = Limiter(60, 600)             # всё без сессии и все неудачные сессии с одного адреса
LIM_LOGIN_IP = Limiter(2, 60)         # входы и регистрации с одного адреса: 60 разом, дальше 2 в секунду
LIM_LOGIN_ACC = Limiter(1 / 60, 10)   # неверные пароли к логину с одного адреса: 10, потом раз в минуту
LIM_REGISTER = Limiter(20, 3000)      # предохранитель на весь сервер — заметно выше лимита одного адреса
LIMITERS = (LIM_SESSION, LIM_STAFF, LIM_IP, LIM_LOGIN_IP, LIM_LOGIN_ACC, LIM_REGISTER)

_local = threading.local()


def db():
    """Одно соединение на поток: открывать его заново на каждый запрос дорого."""
    con = getattr(_local, "con", None)
    if con is None:
        con = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
        con.row_factory = sqlite3.Row
        for pragma in ("foreign_keys=ON", "busy_timeout=10000", "synchronous=NORMAL",
                       "cache_size=-1024", "temp_store=MEMORY"):
            con.execute("PRAGMA " + pragma)
        _local.con = con
    return con


def token_key(tok):
    """В базе лежит не сам токен сессии, а его хеш: утечка файла базы не даёт войти за учеников."""
    return hashlib.sha256(tok.encode()).hexdigest()


HASH_TOKENS = True


def columns(con, table):
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


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
        CREATE TABLE IF NOT EXISTS orders(
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          size TEXT NOT NULL, amount INTEGER NOT NULL, free INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'pending',      -- pending → paid → served | failed | expired | cancelled
          provider TEXT NOT NULL, ext_id TEXT UNIQUE NOT NULL, payment_id TEXT,
          qr_data TEXT, checkout_url TEXT, app_links TEXT,
          via TEXT NOT NULL DEFAULT 'app', staff_id INTEGER REFERENCES users(id),
          num INTEGER, test INTEGER NOT NULL DEFAULT 0,
          created INTEGER NOT NULL, expires INTEGER NOT NULL, checked INTEGER NOT NULL DEFAULT 0,
          paid_at INTEGER, served_at INTEGER, served_by INTEGER REFERENCES users(id));
        CREATE INDEX IF NOT EXISTS orders_status ON orders(status, paid_at);
        CREATE INDEX IF NOT EXISTS orders_user ON orders(user_id, created);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
        """)
        # Миграции старой базы: способ оплаты и сумма у операции, отключение сотрудника.
        oc = columns(con, "ops")
        if "pay" not in oc:
            con.execute("ALTER TABLE ops ADD COLUMN pay TEXT NOT NULL DEFAULT 'cash'")
        if "amount" not in oc:
            con.execute("ALTER TABLE ops ADD COLUMN amount INTEGER NOT NULL DEFAULT 0")
            for sz in SIZES:
                con.execute("UPDATE ops SET amount=? WHERE kind='add' AND size=?", (DEFAULTS["price_" + sz], sz))
        if "order_id" not in oc:
            con.execute("ALTER TABLE ops ADD COLUMN order_id INTEGER")
        if "test" not in oc:
            con.execute("ALTER TABLE ops ADD COLUMN test INTEGER NOT NULL DEFAULT 0")
        if "undone_by" not in oc:
            con.execute("ALTER TABLE ops ADD COLUMN undone_by INTEGER")
            con.execute("ALTER TABLE ops ADD COLUMN undone_at INTEGER")
        if "active" not in columns(con, "users"):
            con.execute("ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        # Системный «сотрудник» — от его имени записываются оплаты, пришедшие от банка.
        if not con.execute("SELECT 1 FROM users WHERE role='system'").fetchone():
            con.execute("INSERT INTO users(role,login,name,pass,code,created) VALUES('system','~qr','Оплата QR',?,?,?)",
                        ("$", new_code(con), int(time.time())))


_rules = {}


def rules(con):
    """Правила читаются на каждом запросе — держим их в памяти, сбрасываем при сохранении настроек."""
    if not _rules:
        r = dict(DEFAULTS)
        for row in con.execute("SELECT key, value FROM settings"):
            r[row["key"]] = row["value"]
        _rules.update(r)
    return dict(_rules)


def public_rules(r):
    return {"perCup": r["per_cup"], "freeCost": r["free_cost"],
            "prices": {"s": r["price_s"], "l": r["price_l"]}, "pay": PAY_MODE}


def system_id(con):
    return con.execute("SELECT id FROM users WHERE role='system'").fetchone()["id"]


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()
    return f"{salt}${h}"


def check_pw(pw, stored):
    salt, _ = stored.split("$", 1)
    if not salt:
        return False
    return hmac.compare_digest(hash_pw(pw, salt), stored)


def slow_hash(fn, *args):
    """Хеширование пароля — не больше двух одновременно. Остальные ждут полсекунды и получают «занято»:
    очередь входов не должна занимать потоки, которые обслуживают кассу."""
    if not hash_slots.acquire(timeout=0.5):
        raise ApiError(503, "Сервер занят, попробуйте через минуту")
    try:
        return fn(*args)
    finally:
        hash_slots.release()


def norm_login(s):
    """Телефон → 996XXXXXXXXX; логин сотрудника — как есть, в нижнем регистре."""
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


def day_start(ts=None):
    d = datetime.fromtimestamp(ts or time.time(), TZ)
    return int(d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def public_user(u):
    return {"name": u["name"], "group": u["grp"], "code": u["code"], "points": u["points"],
            "role": u["role"], "login": u["login"]}


def public_card(u):
    return {"name": u["name"], "group": u["grp"], "code": u["code"], "points": u["points"]}


def op_row(o):
    return {"id": o["id"], "kind": o["kind"], "size": o["size"], "delta": o["delta"],
            "ts": o["ts"], "undone": bool(o["undone"]), "pay": o["pay"], "amount": o["amount"]}


def order_row(o, full=False):
    d = {"id": o["id"], "num": o["num"], "size": o["size"], "amount": o["amount"], "free": bool(o["free"]),
         "status": o["status"], "provider": o["provider"], "via": o["via"], "test": bool(o["test"]),
         "created": o["created"], "expires": o["expires"], "paidAt": o["paid_at"], "servedAt": o["served_at"],
         "pickup": PICKUP + o["ext_id"]}
    if full:
        d.update(qrData=o["qr_data"], checkoutUrl=o["checkout_url"], appLinks=json.loads(o["app_links"] or "{}"))
    return d


class ApiError(Exception):
    def __init__(self, status, msg):
        self.status, self.msg = status, msg


# ---------- GoPay ----------
def gopay_sign(secret, nonce, body):
    return hmac.new(secret.encode(), (nonce + "\n" + body + "\n").encode(), hashlib.sha512).hexdigest().upper()


def gopay_call(path, data):
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    nonce = secrets.token_hex(16)
    req = urllib.request.Request(GOPAY_URL + path, data=body.encode(), method="POST", headers={
        "Content-Type": "application/json", "GoPay-Api-Key": GOPAY_KEY, "GoPay-Nonce": nonce,
        "GoPay-Signature": gopay_sign(GOPAY_SECRET, nonce, body)})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            res = json.loads(r.read() or b"{}")
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"GoPay {path}: {e!r}\n")
        raise ApiError(502, "Платёжный сервис не отвечает. Попробуйте ещё раз")
    if res.get("status") != "OK":
        sys.stderr.write(f"GoPay {path}: {res}\n")
        raise ApiError(502, "Платёжный сервис отказал: " + str(res.get("error_message") or res.get("code")))
    return res.get("data") or {}


def same_amount(d, o):
    """Сумма от банка — ровно наша (140 или 140.00, не 140.99), валюта — сомы, если банк её указал."""
    amount = str(d.get("amount", "")).strip()
    if d.get("currency") and str(d["currency"]).upper() not in ("KGS", "SOM"):
        return False
    return amount in (str(o["amount"]), f"{o['amount']}.00", f"{o['amount']}.0")


def safe_url(u):
    """Ссылки на оплату от провайдера: только https и схемы приложений банков, без javascript: и data:."""
    u = str(u or "")
    return u if re.match(r"^(https://|[a-z][a-z0-9+.-]*://)", u, re.I) and not re.match(r"^(javascript|data|vbscript|file):", u, re.I) else ""


GOPAY_STATUS = {"COMMITTED": "paid", "FAILED": "failed", "EXPIRED": "expired", "CANCELLED": "cancelled"}


DESK = ("desk", "demo")  # заказы с оплатой на кассе (demo — то же, но не деньги)


def settle(con, o, new_status, test=False, pay="qr"):
    """Перевести заказ из pending в итоговый статус. Оплата даёт номер заказа и пишет деньги в выручку,
    но баллы пока 0 — их начислит бариста, когда отсканирует QR ученика и выдаст заказ (serve_order).
    Повторный вызов (повтор вебхука, параллельный опрос) ничего не меняет."""
    o = con.execute("SELECT * FROM orders WHERE id=?", (o["id"],)).fetchone()
    if o["status"] != "pending":
        return o
    now = int(time.time())
    if new_status != "paid":
        con.execute("UPDATE orders SET status=? WHERE id=?", (new_status, o["id"]))
        return con.execute("SELECT * FROM orders WHERE id=?", (o["id"],)).fetchone()
    num = con.execute("SELECT COUNT(*) FROM orders WHERE paid_at>=?", (day_start(now),)).fetchone()[0] + 1
    con.execute("UPDATE orders SET status='paid', paid_at=?, num=?, test=? WHERE id=?",
                (now, num, int(test or o["test"]), o["id"]))
    con.execute("""INSERT INTO ops(user_id,staff_id,kind,size,delta,ts,pay,amount,order_id,test)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (o["user_id"], o["staff_id"] or system_id(con), "add", o["size"], 0, now, pay,
                 o["amount"], o["id"], int(test or o["test"])))
    return con.execute("SELECT * FROM orders WHERE id=?", (o["id"],)).fetchone()


_static = {}


def static_file(path):
    """Файл страницы из памяти: тело, сжатое тело и ETag. Перечитывается, если файл поменялся."""
    f = ROOT / path
    st = f.stat()
    c = _static.get(path)
    if not c or c[0] != st.st_mtime_ns:
        data = f.read_bytes()
        c = (st.st_mtime_ns, data, gzip.compress(data, 6), '"%s"' % hashlib.sha1(data).hexdigest()[:16])
        _static[path] = c
    return c[1:]


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(self), microphone=(), geolocation=()",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                               "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
                               "media-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "mukr-coffee"
    protocol_version = "HTTP/1.1"  # соединение с Caddy держится открытым — без нового рукопожатия на каждый запрос
    timeout = 10                   # запрос, начатый и не дописанный за 10 с, обрывается

    def handle(self):
        """Ровно один запрос: дальше соединение возвращается в epoll (см. PoolServer)."""
        self.close_connection = True
        self.handle_one_request()

    def log_message(self, fmt, *args):
        if os.environ.get("ACCESS_LOG"):
            sys.stderr.write("%s %s\n" % (self.client_ip(), fmt % args))

    def client_ip(self):
        """Адрес из X-Forwarded-For берём только от своего прокси: снаружи заголовок можно подделать."""
        peer = self.client_address[0]
        try:
            trusted = any(ipaddress.ip_address(peer) in n for n in PROXIES)
        except ValueError:
            trusted = False
        fwd = self.headers.get("X-Forwarded-For") if trusted else None
        ip = fwd.split(",")[-1].strip() if fwd else peer
        if ":" in ip:  # у IPv6 у одного человека целая /64 — лимит считаем по ней
            try:
                return str(ipaddress.ip_network(ip + "/64", strict=False))
            except ValueError:
                pass
        return ip

    def security(self):
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)

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
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 429 or status == 503:
            self.send_header("Retry-After", "5")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        if self.path.startswith("/api/"):
            return self.api("GET")
        if not LIM_IP.take(self.client_ip()):
            return self.send_json(429, {"error": "Слишком часто. Подождите несколько секунд"})
        path = self.path.split("?", 1)[0].lstrip("/") or "index.html"
        if path not in STATIC:
            path = "index.html"
        data, packed, etag = static_file(path)
        self.send_response(304 if self.headers.get("If-None-Match") == etag else 200)
        self.security()
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-cache" if path == "index.html" else "public, max-age=3600")
        self.send_header("Vary", "Accept-Encoding")
        if self.headers.get("If-None-Match") == etag:
            self.send_header("Content-Length", "0")
            return self.end_headers()
        if "gzip" in self.headers.get("Accept-Encoding", ""):
            data = packed
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Type", TYPES.get(Path(path).suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self.api("POST")
        self.send_json(404, {"error": "Нет такой страницы"})

    # ---------- API ----------
    def raw_body(self):
        if getattr(self, "_raw", None) is not None:
            return self._raw
        self._raw = b""  # если ниже ошибка — тело больше не читаем, соединение закроется
        cl = self.headers.get("Content-Length") or "0"
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise ApiError(411, "Нужен Content-Length")
        if not re.fullmatch(r"\d{1,6}", cl):
            self.close_connection = True
            raise ApiError(400, "Неверный запрос")
        n = int(cl)
        if n > 20_000:
            self.close_connection = True
            raise ApiError(413, "Слишком большой запрос")
        # Медленное тело (по байту раз в несколько секунд) не держит поток: общий срок на всё тело.
        buf, until = bytearray(), time.monotonic() + BODY_DEADLINE
        while len(buf) < n:
            left = until - time.monotonic()
            if left <= 0:
                self.close_connection = True
                raise ApiError(408, "Запрос шёл слишком долго")
            self.connection.settimeout(min(left, self.timeout))
            try:
                chunk = self.rfile.read1(n - len(buf))
            except (TimeoutError, socket.timeout):
                chunk = b""
            if not chunk:
                self.close_connection = True
                raise ApiError(408, "Запрос шёл слишком долго")
            buf += chunk
        self.connection.settimeout(self.timeout)
        self._raw = bytes(buf)
        return self._raw

    def body(self):
        try:
            return json.loads(self.raw_body() or b"{}")
        except ValueError:
            raise ApiError(400, "Неверный формат запроса")

    def auth(self, con, staff=False, admin=False):
        h = self.headers.get("Authorization", "")
        tok = h[7:] if h.startswith("Bearer ") else ""
        row = con.execute("""SELECT u.*, s.created AS s_created FROM sessions s JOIN users u ON u.id=s.user_id
                             WHERE s.token=? AND s.created>? AND u.active=1""",
                          (token_key(tok), int(time.time()) - SESSION_TTL)).fetchone() if 20 <= len(tok) <= 100 else None
        u = row if row and (row["role"] == "student" or row["s_created"] > time.time() - STAFF_TTL) else None
        if not u:
            # Подделанные токены считаются к лимиту адреса — иначе заголовок Authorization его обходил бы.
            if not LIM_IP.take(self.client_ip(), 5):
                raise ApiError(429, "Слишком часто. Подождите несколько секунд")
            raise ApiError(401, "Войдите заново")
        lim = LIM_STAFF if u["role"] in ("staff", "admin") else LIM_SESSION
        if not lim.take(u["id"]):
            raise ApiError(429, "Слишком часто. Подождите несколько секунд")
        if admin and u["role"] != "admin":
            raise ApiError(403, "Это доступно только администрации")
        if staff and u["role"] not in ("staff", "admin"):
            raise ApiError(403, "Это действие доступно только сотрудникам")
        return u

    def api(self, method):
        self._raw = None
        route = self.path.split("?", 1)[0][len("/api/"):].strip("/")
        query = dict(p.split("=", 1) for p in self.path.split("?", 1)[1].split("&") if "=" in p) if "?" in self.path else {}
        con = db()
        try:
            # Без сессии — общий лимит по адресу; с сессией лимит считает auth() по самой сессии.
            if not self.headers.get("Authorization") and not LIM_IP.take(self.client_ip()):
                raise ApiError(429, "Слишком часто. Подождите несколько секунд")
            res = self.route(con, method, route, query)
            self.send_json(200, res)
        except ApiError as e:
            self.send_json(e.status, {"error": e.msg})
        except Exception as e:  # noqa: BLE001
            if con.in_transaction:
                con.rollback()
            sys.stderr.write(f"ERROR {method} {route}: {e!r}\n")
            self.send_json(500, {"error": "Сбой на сервере. Попробуйте ещё раз"})
        finally:
            # Тело, которое маршрут не прочитал, вычитываем — иначе оно испортит следующий запрос в соединении.
            if self._raw is None and self.headers.get("Content-Length") not in (None, "0"):
                try:
                    self.raw_body()
                except ApiError:
                    pass

    def route(self, con, m, r, q):
        parts = r.split("/")
        if m == "GET" and r == "rules":
            return public_rules(rules(con))
        if m == "POST" and r == "register":
            return self.register(con, self.body())
        if m == "POST" and r == "login":
            return self.login(con, self.body())
        if m == "POST" and r == "logout":
            h = self.headers.get("Authorization", "")
            with write_lock, con:
                con.execute("DELETE FROM sessions WHERE token=?", (token_key(h[7:]),))
            return {"ok": True}
        if m == "GET" and r == "me":
            return self.me(con, self.auth(con))
        # --- оплата ---
        if m == "POST" and r == "pay/gopay":
            return self.gopay_hook(con)
        if m == "POST" and r == "orders":
            return self.create_order(con, self.auth(con), self.body())
        if m == "GET" and len(parts) == 2 and parts[0] == "orders":
            return self.get_order(con, self.auth(con), parts[1])
        if m == "POST" and len(parts) == 3 and parts[0] == "orders" and parts[2] == "cancel":
            return self.cancel_order(con, self.auth(con), parts[1])
        if m == "POST" and r == "orders/scan":
            return self.scan(con, self.auth(con, staff=True), self.body())
        if m == "GET" and r == "health":
            return {"ok": True}
        if m == "POST" and len(parts) == 3 and parts[0] == "orders" and parts[2] == "serve":
            return self.serve_order(con, self.auth(con, staff=True), parts[1], self.body())
        # --- касса ---
        if m == "GET" and len(parts) == 2 and parts[0] == "card":
            self.auth(con, staff=True)
            u = self.card(con, parts[1])
            return {"card": public_card(u), "orders": self.waiting(con, u)}
        if m == "POST" and len(parts) == 3 and parts[0] == "card" and parts[2] in ("add", "redeem"):
            staff = self.auth(con, staff=True)
            return self.change(con, staff, parts[1], parts[2], self.body())
        if m == "POST" and len(parts) == 3 and parts[0] == "op" and parts[2] == "undo":
            staff = self.auth(con, staff=True)
            return self.undo(con, staff, parts[1])
        if m == "GET" and r == "today":
            return self.today(con, self.auth(con, staff=True))
        if m == "GET" and r == "queue":
            self.auth(con, staff=True)
            return self.queue(con)
        # --- администрация ---
        if parts[0] == "admin":
            admin = self.auth(con, admin=True)
            return self.admin(con, admin, m, parts[1:], q)
        raise ApiError(404, "Нет такого адреса")

    # ---------- ученики ----------
    def card(self, con, code):
        code = re.sub(r"[^A-Za-z0-9]", "", code).upper()
        u = con.execute("SELECT * FROM users WHERE code=? AND role='student'", (code,)).fetchone()
        if not u:
            raise ApiError(404, "Карта не найдена. Проверьте код")
        return u

    def me(self, con, u):
        ops = con.execute("SELECT * FROM ops WHERE user_id=? AND undone=0 AND delta!=0 ORDER BY ts DESC LIMIT 30",
                          (u["id"],)).fetchall()
        cups = con.execute("SELECT COUNT(*) FROM ops WHERE user_id=? AND kind='add' AND undone=0 AND delta>0",
                           (u["id"],)).fetchone()[0]
        free = con.execute("SELECT COUNT(*) FROM ops WHERE user_id=? AND kind='redeem' AND undone=0",
                           (u["id"],)).fetchone()[0]
        active = con.execute("""SELECT * FROM orders WHERE user_id=? AND (status='pending' OR
                                (status='paid' AND paid_at>?)) ORDER BY id DESC LIMIT 1""",
                             (u["id"], int(time.time()) - 3 * 3600)).fetchone()
        return {"user": public_user(u), "history": [op_row(o) for o in ops], "cups": cups, "free": free,
                "order": order_row(active, full=True) if active else None}

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
        if not 6 <= len(pw) <= 128:
            raise ApiError(400, "Пароль — от 6 до 128 символов")
        if b.get("website"):  # невидимое поле-ловушка: человек его не заполняет, бот — да
            raise ApiError(400, "Не получилось. Обновите страницу")
        if not LIM_LOGIN_IP.take(self.client_ip()) or not LIM_REGISTER.take("all"):
            raise ApiError(429, "Слишком много регистраций. Попробуйте через минуту")
        if con.execute("SELECT 1 FROM users WHERE login=?", (login,)).fetchone():
            raise ApiError(409, "Этот телефон уже зарегистрирован. Войдите")
        stored = slow_hash(hash_pw, pw)  # вне блокировки записи: хеш считается ~0,1 с
        with write_lock, con:
            if con.execute("SELECT 1 FROM users WHERE login=?", (login,)).fetchone():
                raise ApiError(409, "Этот телефон уже зарегистрирован. Войдите")
            cur = con.execute("INSERT INTO users(role,login,name,grp,pass,code,created) VALUES('student',?,?,?,?,?,?)",
                              (login, name, grp, stored, new_code(con), int(time.time())))
            return self.session(con, cur.lastrowid)

    def login(self, con, b):
        login = norm_login(b.get("phone"))[:40]
        pw = str(b.get("password", ""))[:128]
        ip = self.client_ip()
        if not LIM_LOGIN_IP.take(ip):
            raise ApiError(429, "Слишком много попыток с этой сети. Подождите минуту")
        # Подбор пароля: 10 ошибок к одному логину с одного адреса — дальше раз в минуту. Ключ «логин + адрес»,
        # чтобы чужой человек не мог заблокировать вход владельцу. Жетон берём до проверки и возвращаем при успехе.
        acc = login + "|" + ip
        if not LIM_LOGIN_ACC.take(acc):
            raise ApiError(429, "Слишком много неверных паролей. Подождите минуту")
        u = con.execute("SELECT * FROM users WHERE login=? AND role!='system'", (login,)).fetchone()
        # Для несуществующего логина тоже считаем хеш — по времени ответа не узнать, есть ли такой номер.
        ok = slow_hash(check_pw, pw, u["pass"] if u else "00" * 16 + "$")
        if not u or not ok:
            raise ApiError(401, "Неверный логин или пароль")
        LIM_LOGIN_ACC.give(acc)
        if not u["active"]:
            raise ApiError(403, "Доступ отключён. Обратитесь к администрации")
        if u["role"] != "student" and (self.headers.get("Origin") or "").endswith(".github.io"):
            # Токен кассы не должен лежать в браузере на общем адресе github.io — только на своём сервере.
            raise ApiError(403, "Касса и кабинет открываются только на " + (PUBLIC_URL or "сервере кофейни"))
        with write_lock, con:
            return self.session(con, u["id"])

    def session(self, con, uid):
        tok = secrets.token_urlsafe(32)
        con.execute("INSERT INTO sessions VALUES(?,?,?)", (token_key(tok), uid, int(time.time())))
        u = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return {"token": tok, "user": public_user(u)}

    # ---------- заказы и оплата ----------
    def create_order(self, con, u, b):
        """Ученик заказывает сам (via=app) или бариста выставляет QR на кассе (via=desk, нужен code)."""
        size = b.get("size")
        if size not in SIZES:
            raise ApiError(400, "Выберите размер: маленький или большой")
        via, staff_id = "app", None
        if u["role"] in ("staff", "admin"):
            via, staff_id = "desk", u["id"]
            u = self.card(con, str(b.get("code", "")))
        elif u["role"] != "student":
            raise ApiError(403, "Нет доступа")
        r = rules(con)
        free = bool(b.get("free"))
        now = int(time.time())
        with write_lock, con:
            if con.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND created>?", (u["id"], now - 3600)).fetchone()[0] >= 20:
                raise ApiError(429, "Слишком много заказов за час. Подождите немного")
            # Старые неоплаченные заказы этого ученика закрываем — активным остаётся один.
            con.execute("UPDATE orders SET status='cancelled' WHERE user_id=? AND status='pending'", (u["id"],))
            if free:
                fresh = con.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
                if fresh["points"] < r["free_cost"]:
                    raise ApiError(409, f"Не хватает баллов: на карте {fresh['points']}, нужно {r['free_cost']}")
                num = con.execute("SELECT COUNT(*) FROM orders WHERE paid_at>=?", (day_start(now),)).fetchone()[0] + 1
                cur = con.execute("""INSERT INTO orders(user_id,size,amount,free,status,provider,ext_id,via,staff_id,num,
                                     created,expires,paid_at) VALUES(?,?,0,1,'paid','points',?,?,?,?,?,?,?)""",
                                  (u["id"], size, "MC-F-" + secrets.token_hex(6), via, staff_id, num, now, now, now))
                con.execute("UPDATE users SET points=points-? WHERE id=?", (r["free_cost"], u["id"]))
                con.execute("""INSERT INTO ops(user_id,staff_id,kind,size,delta,ts,pay,amount,order_id)
                               VALUES(?,?,?,?,?,?,?,0,?)""",
                            (u["id"], staff_id or system_id(con), "redeem", size, -r["free_cost"], now, "points", cur.lastrowid))
                return {"order": order_row(con.execute("SELECT * FROM orders WHERE id=?", (cur.lastrowid,)).fetchone(), True)}
            if PAY_MODE == "off":
                raise ApiError(503, "Оплата по QR пока не подключена. Оплатите на кассе")
            amount = r["price_" + size]
            ext = f"MC-{now}-{secrets.token_hex(4)}"
            provider = "gopay" if PAY_MODE == "gopay" else "desk"
            cur = con.execute("""INSERT INTO orders(user_id,size,amount,provider,ext_id,via,staff_id,test,created,expires)
                                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
                              (u["id"], size, amount, provider, ext, via, staff_id, int(PAY_MODE == "demo"),
                               now, now + ORDER_TTL))
            oid = cur.lastrowid
        if PAY_MODE == "gopay":
            # Запрос в GoPay — вне блокировки базы: сеть может отвечать секунды.
            req = {"order_id": ext, "amount": f"{amount}.00", "lifetime": ORDER_TTL,
                   "description": f"МУКР Кофе · {'большой' if size == 'l' else 'маленький'}"}
            if PUBLIC_URL:
                req["callback_url"] = PUBLIC_URL + "/api/pay/gopay"
            try:
                d = gopay_call("/v1/payments", req)
            except ApiError:
                with write_lock, con:
                    con.execute("UPDATE orders SET status='failed' WHERE id=?", (oid,))
                raise
            with write_lock, con:
                con.execute("""UPDATE orders SET payment_id=?, qr_data=?, checkout_url=?, app_links=?, test=? WHERE id=?""",
                            (d.get("payment_id"), d.get("qr_data"), safe_url(d.get("checkout_url")),
                             json.dumps({k: v for k, v in (d.get("app_links") or {}).items() if safe_url(v)}),
                             int(bool(d.get("testing_mode"))), oid))
        return {"order": order_row(con.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone(), True)}

    def own_order(self, con, u, oid):
        o = con.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone() if str(oid).isdigit() else None
        if not o or (u["role"] == "student" and o["user_id"] != u["id"]):
            raise ApiError(404, "Заказ не найден")
        return o

    def get_order(self, con, u, oid):
        o = self.own_order(con, u, oid)
        now = int(time.time())
        if o["status"] == "pending":
            if now > o["expires"]:
                with write_lock, con:
                    o = settle(con, o, "expired")
            elif o["provider"] == "gopay" and now - o["created"] >= 5 and now - o["checked"] >= 5:
                # Страховка, если вебхук задержался: раз в 5 секунд спрашиваем статус у GoPay сами.
                with write_lock, con:
                    con.execute("UPDATE orders SET checked=? WHERE id=?", (now, o["id"]))
                try:
                    d = gopay_call("/v1/payments/query", {"order_id": o["ext_id"]})
                    st = GOPAY_STATUS.get(d.get("status"))
                    if st and (st != "paid" or same_amount(d, o)):
                        with write_lock, con:
                            o = settle(con, o, st, test=bool(d.get("testing_mode")))
                except ApiError:
                    pass
        res = {"order": order_row(o, full=o["status"] == "pending")}
        if o["status"] in ("paid", "served"):
            res["points"] = con.execute("SELECT points FROM users WHERE id=?", (o["user_id"],)).fetchone()["points"]
        return res

    def cancel_order(self, con, u, oid):
        o = self.own_order(con, u, oid)
        if o["status"] != "pending":
            raise ApiError(409, "Заказ уже оплачен или закрыт")
        if o["provider"] == "gopay":
            try:
                gopay_call("/v1/payments/cancel", {"order_id": o["ext_id"]})
            except ApiError:
                pass  # если банк уже провёл оплату — придёт вебхук, и заказ станет оплаченным
        with write_lock, con:
            o = settle(con, o, "cancelled")
        return {"order": order_row(o)}

    def waiting(self, con, u):
        """Заказы ученика, которые ждут бариста: оплаченные банком или за баллы и ещё не оплаченные на кассе."""
        rows = con.execute("""SELECT * FROM orders WHERE user_id=? AND (status='paid' OR
                              (status='pending' AND provider IN ('desk','demo') AND expires>?)) ORDER BY created""",
                           (u["id"], int(time.time()))).fetchall()
        return [order_row(o) for o in rows]

    def scan(self, con, staff, b):
        """Бариста отсканировал QR с телефона ученика — личный (карта) или старый QR заказа.
        Ничего не меняет: возвращает карту и заказы, которые ждут. Выдача — отдельной кнопкой (serve_order)."""
        qr = str(b.get("qr", "")).strip()[:120]
        if qr.startswith(PICKUP):
            o = con.execute("SELECT * FROM orders WHERE ext_id=?", (qr[len(PICKUP):],)).fetchone()
            u = con.execute("SELECT * FROM users WHERE id=?", (o["user_id"],)).fetchone() if o else None
            if not u:
                raise ApiError(404, "Заказ не найден")
        else:
            u = self.card(con, qr[len(CARD_QR):] if qr.startswith(CARD_QR) else qr)
        return {"card": public_card(u), "orders": self.waiting(con, u)}

    def serve_order(self, con, staff, oid, b):
        """Выдача только по отсканированному QR ученика: код карты должен совпасть с заказом.
        Заказ «оплата на кассе» бариста закрывает, отметив способ оплаты, — тогда и деньги, и баллы.
        Баллы начисляются здесь, а не при оплате."""
        o = self.own_order(con, staff, oid)
        code = re.sub(r"[^A-Za-z0-9]", "", str(b.get("code", ""))).upper()
        u = con.execute("SELECT * FROM users WHERE id=?", (o["user_id"],)).fetchone()
        if not code:
            raise ApiError(400, "Сначала отсканируйте QR ученика")
        if code != u["code"]:
            raise ApiError(409, "Это QR другого ученика — заказ не его")
        pay = b.get("pay")
        with write_lock, con:
            o = con.execute("SELECT * FROM orders WHERE id=?", (o["id"],)).fetchone()
            if o["status"] == "pending" and o["provider"] in DESK:
                if o["expires"] < time.time():
                    raise ApiError(409, "Заказ просрочен — пусть ученик оформит заново")
                if pay not in ("cash", "card"):
                    raise ApiError(400, "Отметьте, как ученик заплатил: наличными или картой")
                o = settle(con, o, "paid", test=o["test"], pay=pay)
            elif o["status"] == "pending":
                raise ApiError(409, "Заказ ещё не оплачен — пусть ученик оплатит в банке")
            return self.hand_over(con, staff, o)

    def hand_over(self, con, staff, o):
        """Выдача (вызывать под write_lock): заказ → served, за оплаченный стакан +баллы."""
        now = int(time.time())
        if con.execute("SELECT status FROM orders WHERE id=?", (o["id"],)).fetchone()["status"] != "paid":
            raise ApiError(409, "Этот заказ не ждёт выдачи")
        u = con.execute("SELECT * FROM users WHERE id=?", (o["user_id"],)).fetchone()
        con.execute("UPDATE orders SET status='served', served_at=?, served_by=? WHERE id=?",
                    (now, staff["id"], o["id"]))
        delta = 0
        op = con.execute("SELECT * FROM ops WHERE order_id=? AND kind='add' AND undone=0", (o["id"],)).fetchone()
        if op and op["delta"] == 0:  # заказы, оплаченные до этой версии, баллы уже получили
            delta = rules(con)["per_cup"]
            con.execute("UPDATE users SET points=points+? WHERE id=?", (delta, u["id"]))
            con.execute("UPDATE ops SET delta=?, ts=?, staff_id=? WHERE id=?", (delta, now, staff["id"], op["id"]))
        u = con.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
        o = con.execute("SELECT * FROM orders WHERE id=?", (o["id"],)).fetchone()
        return {"ok": True, "delta": delta, "card": public_card(u), "orders": self.waiting(con, u), "order": order_row(o)}

    def gopay_hook(self, con):
        """Вебхук GoPay: и типизированный (events_url), и старый (callback_url). Подпись обязательна."""
        raw = self.raw_body()
        nonce = self.headers.get("GoPay-Nonce", "")
        sig = self.headers.get("GoPay-Signature", "").upper()
        body = raw.decode("utf-8", "replace")
        good = [s for s in (GOPAY_HOOK_SECRET, GOPAY_SECRET) if s]
        if not nonce or not sig or not any(hmac.compare_digest(gopay_sign(s, nonce, body), sig) for s in good):
            raise ApiError(401, "Неверная подпись")
        try:
            ev = json.loads(raw)
        except ValueError:
            raise ApiError(400, "Неверный формат")
        d = ev.get("data") if isinstance(ev.get("data"), dict) else ev
        if ev.get("event") == "test.ping" or not d.get("order_id"):
            return {"ok": True}
        status = GOPAY_STATUS.get(d.get("status") or "") or {
            "payment.committed": "paid", "payment.failed": "failed",
            "payment.expired": "expired", "payment.cancelled": "cancelled"}.get(ev.get("event") or "")
        o = con.execute("SELECT * FROM orders WHERE ext_id=?", (str(d["order_id"]),)).fetchone()
        if not o or not status:
            return {"ok": True}  # чужой или неизвестный — подтверждаем, чтобы GoPay не повторял
        if status == "paid" and not same_amount(d, o):
            sys.stderr.write(f"GoPay: сумма не сходится по {o['ext_id']}: {d.get('amount')} {d.get('currency')} != {o['amount']}\n")
            raise ApiError(409, "Сумма не совпадает")
        with write_lock, con:
            if status == "paid" and o["status"] in ("expired", "cancelled"):
                # Деньги пришли, когда заказ у нас уже закрылся (истёк срок, ученик открыл новый) — не теряем их:
                # заказ снова становится оплаченным и попадает в очередь к бариста.
                sys.stderr.write(f"GoPay: поздняя оплата закрытого заказа {o['ext_id']} — восстановлен\n")
                con.execute("UPDATE orders SET status='pending' WHERE id=?", (o["id"],))
            settle(con, o, status, test=bool(d.get("testing_mode")))
        return {"ok": True}

    # ---------- касса ----------
    def change(self, con, staff, code, kind, b):
        size = b.get("size")
        if size not in SIZES:
            raise ApiError(400, "Выберите размер: маленький или большой")
        r = rules(con)
        with write_lock, con:
            u = self.card(con, code)
            if kind == "add":
                delta, amount, pay = r["per_cup"], r["price_" + size], "card" if b.get("pay") == "card" else "cash"
            else:
                if u["points"] < r["free_cost"]:
                    raise ApiError(409, f"Не хватает баллов: на карте {u['points']}, нужно {r['free_cost']}")
                delta, amount, pay = -r["free_cost"], 0, "points"
            con.execute("UPDATE users SET points=points+? WHERE id=?", (delta, u["id"]))
            cur = con.execute("""INSERT INTO ops(user_id,staff_id,kind,size,delta,ts,pay,amount)
                                 VALUES(?,?,?,?,?,?,?,?)""",
                              (u["id"], staff["id"], kind, size, delta, int(time.time()), pay, amount))
            u = con.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
            return {"card": public_card(u), "op": {"id": cur.lastrowid, "kind": kind, "size": size, "delta": delta}}

    def undo(self, con, staff, op_id):
        with write_lock, con:
            o = con.execute("SELECT * FROM ops WHERE id=?", (op_id,)).fetchone()
            if not o or o["undone"]:
                raise ApiError(404, "Операция уже отменена или не найдена")
            if staff["role"] != "admin":
                # Бариста отменяет только ошибочную продажу на кассе: не заказ, не списание за баллы, не чужую.
                if o["pay"] == "qr" or o["kind"] == "redeem" or o["order_id"]:
                    raise ApiError(409, "Это может отменить только администрация")
                if o["staff_id"] != staff["id"]:
                    raise ApiError(409, "Отменить можно только свою продажу")
            if time.time() - o["ts"] > UNDO_WINDOW and staff["role"] != "admin":
                raise ApiError(409, "Отменить можно в течение 10 минут")
            u = con.execute("SELECT * FROM users WHERE id=?", (o["user_id"],)).fetchone()
            if u["points"] - o["delta"] < 0:
                raise ApiError(409, "Баллы уже потрачены — отменить нельзя")
            con.execute("UPDATE users SET points=points-? WHERE id=?", (o["delta"], u["id"]))
            con.execute("UPDATE ops SET undone=1, undone_by=?, undone_at=? WHERE id=?", (staff["id"], int(time.time()), op_id))
            u = con.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
            return {"card": public_card(u)}

    def can_undo(self, staff, r):
        """Та же проверка, что в undo(): кнопку «Отменить» показываем, только если отмена пройдёт."""
        if r["undone"]:
            return False
        if staff["role"] == "admin":
            return True
        return (r["pay"] != "qr" and r["kind"] != "redeem" and not r["order_id"] and r["staff_id"] == staff["id"]
                and time.time() - r["ts"] <= UNDO_WINDOW)

    def today(self, con, staff):
        start = day_start()
        rows = con.execute("""SELECT o.*, u.name, u.grp, u.code, b.name AS undone_name FROM ops o JOIN users u ON u.id=o.user_id
                              LEFT JOIN users b ON b.id=o.undone_by
                              WHERE o.ts>=? ORDER BY o.ts DESC LIMIT 200""", (start,)).fetchall()
        live = [r for r in rows if not r["undone"]]
        money = [r for r in live if r["kind"] == "add" and not r["test"]]
        return {"ops": [dict(op_row(r), name=r["name"], group=r["grp"], code=r["code"], test=bool(r["test"]),
                             canUndo=self.can_undo(staff, r), undoneBy=r["undone_name"])
                        for r in rows if r["delta"]],  # оплачен, но ещё не выдан — в ленте не показываем
                "sold": sum(1 for r in live if r["kind"] == "add"),
                "free": sum(1 for r in live if r["kind"] == "redeem"),
                "revenue": sum(r["amount"] for r in money),
                "qr": sum(r["amount"] for r in money if r["pay"] == "qr"),
                "card": sum(r["amount"] for r in money if r["pay"] == "card"),
                "cash": sum(r["amount"] for r in money if r["pay"] == "cash")}

    def queue(self, con):
        """Заказы, которые ждут выдачи (оплаченные и демо, ждущие сканирования), и последние выданные."""
        start, now = day_start(), int(time.time())
        q = """SELECT o.*, u.name, u.grp FROM orders o JOIN users u ON u.id=o.user_id WHERE """
        wait = con.execute(q + """(o.status='paid' AND o.paid_at>=?) OR (o.status='pending' AND o.provider IN ('desk','demo') AND o.expires>?)
                                  ORDER BY o.created""", (start - 6 * 3600, now)).fetchall()
        done = con.execute(q + "o.status='served' AND o.served_at>=? ORDER BY o.served_at DESC LIMIT 8", (start,)).fetchall()
        pend = con.execute("SELECT COUNT(*) FROM orders WHERE status='pending' AND provider='gopay' AND expires>?", (now,)).fetchone()[0]
        row = lambda o: dict(order_row(o), name=o["name"], group=o["grp"])
        return {"waiting": [row(o) for o in wait], "served": [row(o) for o in done], "pending": pend}

    # ---------- администрация ----------
    def admin(self, con, admin, m, p, q):
        if m == "GET" and p == ["stats"]:
            days = q.get("days", "30")
            return self.stats(con, int(days) if days.isdigit() else 30, q.get("test") == "1")
        if m == "GET" and p == ["orders"]:
            rows = con.execute("""SELECT o.*, u.name, u.grp FROM orders o JOIN users u ON u.id=o.user_id
                                  ORDER BY o.id DESC LIMIT 100""").fetchall()
            return {"orders": [dict(order_row(o), name=o["name"], group=o["grp"]) for o in rows]}
        if m == "GET" and p == ["staff"]:
            start = day_start()
            rows = con.execute("""SELECT u.*, (SELECT COUNT(*) FROM ops WHERE staff_id=u.id AND ts>=? AND undone=0) AS today
                                  FROM users u WHERE role IN ('staff','admin') ORDER BY role DESC, name""", (start,)).fetchall()
            return {"staff": [{"id": u["id"], "name": u["name"], "login": u["login"], "role": u["role"],
                               "active": bool(u["active"]), "today": u["today"], "me": u["id"] == admin["id"]} for u in rows]}
        if m == "POST" and p == ["staff"]:
            b = self.body()
            login, name = norm_login(b.get("login")), " ".join(str(b.get("name", "")).split())[:60]
            role = "admin" if b.get("role") == "admin" else "staff"
            if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,23}", login):
                raise ApiError(400, "Логин — латиницей, от 3 символов, например aigerim")
            if len(name) < 2:
                raise ApiError(400, "Напишите имя сотрудника")
            pw = secrets.token_urlsafe(6)
            with write_lock, con:
                if con.execute("SELECT 1 FROM users WHERE login=?", (login,)).fetchone():
                    raise ApiError(409, "Такой логин уже есть")
                con.execute("INSERT INTO users(role,login,name,pass,code,created) VALUES(?,?,?,?,?,?)",
                            (role, login, name, hash_pw(pw), new_code(con), int(time.time())))
            return {"login": login, "password": pw}
        if m == "POST" and len(p) == 3 and p[0] == "staff" and p[2] in ("reset", "toggle"):
            with write_lock, con:
                u = con.execute("SELECT * FROM users WHERE id=? AND role IN ('staff','admin')", (p[1],)).fetchone()
                if not u:
                    raise ApiError(404, "Сотрудник не найден")
                if u["id"] == admin["id"] and p[2] == "toggle":
                    raise ApiError(409, "Себя отключить нельзя")
                if u["role"] == "admin" and u["id"] != admin["id"]:
                    raise ApiError(403, "Пароль и доступ другого администратора меняются только на сервере")
                con.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
                if p[2] == "reset":
                    pw = secrets.token_urlsafe(6)
                    con.execute("UPDATE users SET pass=? WHERE id=?", (hash_pw(pw), u["id"]))
                    return {"login": u["login"], "password": pw}
                con.execute("UPDATE users SET active=1-active WHERE id=?", (u["id"],))
                return {"ok": True}
        if m == "GET" and p == ["students"]:
            # Поиск по имени, телефону или коду карты; без запроса — последние зарегистрированные.
            qs = re.sub(r"[%_]", "", urllib.parse.unquote_plus(q.get("q", "")).strip())[:40]
            sql = "SELECT * FROM users WHERE role='student'"
            args = []
            if qs:
                digits = re.sub(r"\D", "", qs)
                sql += " AND (name LIKE ? OR code=? OR grp LIKE ?" + (" OR login LIKE ?" if len(digits) >= 3 else "") + ")"
                args = [f"%{qs}%", re.sub(r"[^A-Za-z0-9]", "", qs).upper(), f"%{qs}%"] + ([f"%{digits[-9:]}%"] if len(digits) >= 3 else [])
            rows = con.execute(sql + " ORDER BY id DESC LIMIT 50", args).fetchall()
            total = con.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0]
            return {"total": total, "students": [dict(public_card(u), id=u["id"], phone="+" + u["login"], active=bool(u["active"]),
                                                      created=u["created"]) for u in rows]}
        if m == "POST" and len(p) == 3 and p[0] == "students" and p[2] in ("reset", "toggle"):
            with write_lock, con:
                u = con.execute("SELECT * FROM users WHERE id=? AND role='student'", (p[1],)).fetchone()
                if not u:
                    raise ApiError(404, "Ученик не найден")
                if p[2] == "toggle":
                    con.execute("UPDATE users SET active=? WHERE id=?", (0 if u["active"] else 1, u["id"]))
                    con.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
                    return {"active": not u["active"]}
                pw = "".join(secrets.choice("23456789") for _ in range(6))
                con.execute("UPDATE users SET pass=? WHERE id=?", (hash_pw(pw), u["id"]))
                con.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
                return {"password": pw}
        if m == "POST" and p == ["settings"]:
            b = self.body()
            new = {}
            for k, lo, hi in (("price_s", 10, 2000), ("price_l", 10, 2000), ("per_cup", 1, 100), ("free_cost", 1, 1000)):
                try:
                    v = int(b.get(k))
                except (TypeError, ValueError):
                    raise ApiError(400, "Заполните все поля числами")
                if not lo <= v <= hi:
                    raise ApiError(400, "Проверьте значения: они вне разумных пределов")
                new[k] = v
            if new["free_cost"] % new["per_cup"]:
                raise ApiError(400, "Баллы за бесплатный кофе должны делиться на баллы за стакан без остатка")
            if new["free_cost"] // new["per_cup"] > 12:
                raise ApiError(400, "На карте не больше 12 штампов")
            with write_lock, con:
                for k, v in new.items():
                    con.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
            _rules.clear()
            return public_rules(rules(con))
        raise ApiError(404, "Нет такого адреса")

    def stats(self, con, days, with_test):
        days = max(1, min(days, 90))
        now = int(time.time())
        start = day_start(now) - (days - 1) * 86400
        t = "" if with_test else " AND o.test=0"
        rows = con.execute(f"""SELECT o.* FROM ops o WHERE o.ts>=? AND o.undone=0{t}""", (start,)).fetchall()
        series = []
        for i in range(days):
            a = start + i * 86400
            day = [r for r in rows if a <= r["ts"] < a + 86400]
            series.append({"day": datetime.fromtimestamp(a, TZ).strftime("%Y-%m-%d"),
                           "qr": sum(r["amount"] for r in day if r["kind"] == "add" and r["pay"] == "qr"),
                           "cash": sum(r["amount"] for r in day if r["kind"] == "add" and r["pay"] == "cash"),
                           "card": sum(r["amount"] for r in day if r["kind"] == "add" and r["pay"] == "card"),
                           "cups": sum(1 for r in day if r["kind"] == "add"),
                           "free": sum(1 for r in day if r["kind"] == "redeem")})
        adds = [r for r in rows if r["kind"] == "add"]
        top = con.execute(f"""SELECT u.name, u.grp, COUNT(*) AS cups FROM ops o JOIN users u ON u.id=o.user_id
                              WHERE o.ts>=? AND o.undone=0 AND o.kind='add'{t} GROUP BY u.id ORDER BY cups DESC LIMIT 5""",
                          (start,)).fetchall()
        by_staff = con.execute(f"""SELECT u.name, u.role, COUNT(*) AS n, SUM(o.amount) AS sum FROM ops o JOIN users u ON u.id=o.staff_id
                                   WHERE o.ts>=? AND o.undone=0 AND o.kind='add'{t} GROUP BY u.id ORDER BY n DESC""",
                               (start,)).fetchall()
        students = con.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0]
        new_students = con.execute("SELECT COUNT(*) FROM users WHERE role='student' AND created>=?", (start,)).fetchone()[0]
        demo = con.execute("SELECT COUNT(*) FROM ops WHERE ts>=? AND test=1 AND undone=0", (start,)).fetchone()[0]
        return {"days": days, "series": series,
                "revenue": sum(r["amount"] for r in adds), "qr": sum(r["amount"] for r in adds if r["pay"] == "qr"),
                "cash": sum(r["amount"] for r in adds if r["pay"] == "cash"),
                "card": sum(r["amount"] for r in adds if r["pay"] == "card"), "cups": len(adds),
                "free": sum(1 for r in rows if r["kind"] == "redeem"),
                "freeCost": sum(rules(con)["price_" + r["size"]] for r in rows if r["kind"] == "redeem"),
                "students": students, "newStudents": new_students, "demoOps": demo, "pay": PAY_MODE,
                "top": [dict(t) for t in top], "byStaff": [dict(s) for s in by_staff]}


class PoolServer(HTTPServer):
    """HTTP-сервер: постоянный пул потоков + epoll для ожидающих соединений.

    Соединение, которое молчит (держит keep-alive или медленно подключилось), не занимает поток:
    оно лежит в epoll, и поток берёт его, только когда пришли данные. Так 32 потока обслуживают
    тысячи открытых соединений, а приём «открыть сотни сокетов и молчать» (slowloris) не работает.
    """
    request_queue_size = 1024
    allow_reuse_address = True
    IDLE = 15            # сколько ждём следующий запрос в открытом соединении, с
    MAX_CONN = 4000      # больше открытых соединений не держим — новые сразу закрываются

    def __init__(self, addr, handler, workers=WORKERS):
        super().__init__(addr, handler)
        self.jobs = queue.Queue()
        self.sel = selectors.DefaultSelector()
        self.parked = queue.SimpleQueue()          # сокеты от потоков → в epoll (сам epoll трогает один поток)
        self.wake_r, self.wake_w = socket.socketpair()
        self.wake_r.setblocking(False)
        self.sel.register(self.wake_r, selectors.EVENT_READ, None)
        self.idle = {}                              # сокет → (адрес, срок)
        threading.Thread(target=self.watch, daemon=True).start()
        for _ in range(workers):
            threading.Thread(target=self.worker, daemon=True).start()

    def park(self, req, addr):
        self.parked.put((req, addr))
        try:
            self.wake_w.send(b"x")
        except OSError:
            pass

    def watch(self):
        next_sweep = 0
        while True:
            for key, _ in self.sel.select(timeout=1):
                if key.data is None:
                    try:
                        self.wake_r.recv(4096)
                    except OSError:
                        pass
                    continue
                self.sel.unregister(key.fileobj)
                self.idle.pop(key.fileobj, None)
                self.jobs.put((key.fileobj, key.data))
            while True:
                try:
                    req, addr = self.parked.get_nowait()
                except queue.Empty:
                    break
                if len(self.idle) >= self.MAX_CONN:
                    self.shutdown_request(req)
                    continue
                self.idle[req] = (addr, time.monotonic() + self.IDLE)
                self.sel.register(req, selectors.EVENT_READ, addr)
            now = time.monotonic()
            if now >= next_sweep:  # просроченные ищем раз в секунду, а не после каждого запроса
                next_sweep = now + 1
                for req, (addr, until) in list(self.idle.items()):
                    if until < now:
                        self.sel.unregister(req)
                        del self.idle[req]
                        self.shutdown_request(req)

    def worker(self):
        while True:
            req, addr = self.jobs.get()
            try:
                h = self.RequestHandlerClass(req, addr, self)
                keep = not h.close_connection
            except Exception:  # noqa: BLE001
                self.handle_error(req, addr)
                keep = False
            if keep:
                self.park(req, addr)
            else:
                self.shutdown_request(req)

    def process_request(self, req, addr):
        req.settimeout(Handler.timeout)
        self.park(req, addr)   # новое соединение тоже сначала ждёт данных в epoll

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        super().server_bind()

    def handle_error(self, req, addr):
        e = sys.exc_info()[1]
        if not isinstance(e, (ConnectionError, TimeoutError, socket.timeout)):
            super().handle_error(req, addr)


def raise_fd_limit():
    """Каждое открытое соединение — дескриптор; 1024 по умолчанию мало для тысяч клиентов."""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(hard, 65536), hard))
    except (ImportError, ValueError, OSError):
        pass


def backup(keep=14):
    """Копия базы средствами SQLite (безопасно на ходу) в data/backups, храним последние keep."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dst = BACKUP_DIR / datetime.now(TZ).strftime("coffee-%Y%m%d-%H%M.db")
    src = sqlite3.connect(DB_PATH, timeout=30)
    out = sqlite3.connect(dst)
    try:
        src.backup(out)
    finally:
        out.close(); src.close()
    os.chmod(dst, 0o600)
    for old in sorted(BACKUP_DIR.glob("coffee-*.db"))[:-keep]:
        old.unlink()
    return dst


def housekeeping():
    """Раз в 10 минут: просроченные заказы закрываем, старые сессии удаляем; раз в сутки — копия базы."""
    last_backup = 0
    while True:
        try:
            con, now = db(), int(time.time())
            with write_lock, con:
                con.execute("UPDATE orders SET status='expired' WHERE status='pending' AND expires<?", (now,))
                con.execute("DELETE FROM sessions WHERE created<?", (now - SESSION_TTL,))
            for lim in LIMITERS:
                lim.sweep()
            if now - last_backup > 86400:
                backup()
                last_backup = now
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"housekeeping: {e!r}\n")
        time.sleep(600)


def cli(args):
    import getpass
    init_db()
    if args[0] in ("--staff", "--admin") and len(args) >= 3:
        role = "admin" if args[0] == "--admin" else "staff"
        login, name = norm_login(args[1]), args[2]
        pw = args[3] if len(args) > 3 else getpass.getpass("Пароль: ")
        with db() as con:
            if con.execute("SELECT 1 FROM users WHERE login=?", (login,)).fetchone():
                sys.exit(f"Логин {login} уже занят")
            con.execute("INSERT INTO users(role,login,name,pass,code,created) VALUES(?,?,?,?,?,?)",
                        (role, login, name, hash_pw(pw), new_code(con), int(time.time())))
        print(f"{'Администратор' if role == 'admin' else 'Бариста'} {name} — логин {login}")
    elif args[0] == "--reset" and len(args) >= 2:
        login = norm_login(args[1])
        pw = args[2] if len(args) > 2 else secrets.token_urlsafe(6)
        with db() as con:
            n = con.execute("UPDATE users SET pass=? WHERE login=? AND role!='system'", (hash_pw(pw), login)).rowcount
            con.execute("DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE login=?)", (login,))
        print(f"Новый пароль для {login}: {pw}" if n else f"Нет пользователя {login}")
    else:
        print(__doc__)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cli(sys.argv[1:])
    else:
        init_db()
        raise_fd_limit()
        threading.Thread(target=housekeeping, daemon=True).start()
        print(f"МУКР Кофе: http://{HOST}:{PORT} · оплата: {PAY_MODE} · потоков: {WORKERS}", flush=True)
        PoolServer((HOST, PORT), Handler).serve_forever()
