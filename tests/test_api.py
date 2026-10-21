"""Проверка API на временной базе: python3 -m unittest discover -s tests"""
import json, os, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ.update(DB_PATH=str(Path(TMP) / "t.db"), PAY_MODE="desk")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server as S  # noqa: E402

S.init_db()
HTTPD = S.PoolServer(("127.0.0.1", 0), S.Handler, workers=8)
BASE = f"http://127.0.0.1:{HTTPD.server_address[1]}/api/"
threading.Thread(target=HTTPD.serve_forever, daemon=True).start()


def call(path, body=None, tok=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": "application/json", **(headers or {})}
    if tok:
        h["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(BASE + path, data=data, headers=h, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def student(phone):
    st, r = call("register", {"name": "Тест Студент", "group": "ПИ-2", "phone": phone, "password": "secret1"})
    assert st == 200, r
    return r["token"], r["user"]["code"]


S.cli(["--staff", "bar", "Бариста", "barpass1"])
S.cli(["--admin", "boss", "Админ", "bosspass1"])
BAR = call("login", {"phone": "bar", "password": "barpass1"})[1]["token"]
BOSS = call("login", {"phone": "boss", "password": "bosspass1"})[1]["token"]


class Api(unittest.TestCase):
    def test_desk_order_waits_for_scan_and_payment(self):
        tok, code = student("0700000001")
        st, r = call("orders", {"size": "l"}, tok)
        self.assertEqual(st, 200)
        o = r["order"]
        self.assertEqual((o["status"], o["amount"]), ("pending", 170))
        time.sleep(0.1)
        st, r = call(f"orders/{o['id']}", tok=tok)
        self.assertEqual(r["order"]["status"], "pending")  # сам не подтверждается
        self.assertEqual(call("me", tok=tok)[1]["user"]["points"], 0)
        self.assertIn(o["id"], [x["id"] for x in call("queue", tok=BAR)[1]["waiting"]])
        self.assertEqual(call("orders/scan", {"qr": S.CARD_QR + code}, tok)[0], 403)  # ученик сам себе не сканирует
        # Скан личного QR показывает заказ, но ничего не меняет.
        st, r = call("orders/scan", {"qr": S.CARD_QR + code}, BAR)
        self.assertEqual((st, [x["id"] for x in r["orders"]], r["card"]["points"]), (200, [o["id"]], 0))
        self.assertEqual(call(f"orders/{o['id']}/serve", {"code": code}, BAR)[0], 400)  # без способа оплаты нельзя
        st, r = call(f"orders/{o['id']}/serve", {"code": code, "pay": "card"}, BAR)
        self.assertEqual((st, r["delta"], r["card"]["points"], r["order"]["status"], r["orders"]), (200, 10, 10, "served", []))
        self.assertTrue(r["order"]["num"] >= 1)
        self.assertEqual(call(f"orders/{o['id']}/serve", {"code": code, "pay": "card"}, BAR)[0], 409)  # второй раз — нет
        st, r = call(f"orders/{o['id']}", tok=tok)
        self.assertEqual((r["order"]["status"], r["points"]), ("served", 10))
        self.assertEqual(call("today", tok=BAR)[1]["card"] >= 170, True)  # деньги картой в кассе дня
        self.assertEqual(call("orders/scan", {"qr": "MUKRCOFFEE:NOPE"}, BAR)[0], 404)

    def test_other_student_cannot_see_order(self):
        tok, _ = student("0700000002")
        tok2, _ = student("0700000003")
        oid = call("orders", {"size": "s"}, tok)[1]["order"]["id"]
        self.assertEqual(call(f"orders/{oid}", tok=tok2)[0], 404)

    def test_queue_and_serve(self):
        tok, code = student("0700000004")
        oid = call("orders", {"size": "s"}, tok)[1]["order"]["id"]
        with S.db() as con:  # оплачено банком
            S.settle(con, con.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone(), "paid")
        q = call("queue", tok=BAR)[1]
        self.assertIn(oid, [o["id"] for o in q["waiting"]])
        self.assertEqual(call(f"orders/{oid}/serve", {}, BAR)[0], 400)  # без сканирования QR нельзя
        _, other = student("0700000014")
        self.assertEqual(call(f"orders/{oid}/serve", {"code": other}, BAR)[0], 409)  # чужой QR
        self.assertEqual(call(f"orders/{oid}/serve", {"code": code}, tok)[0], 403)  # ученик не выдаёт
        self.assertEqual(call(f"orders/{oid}/serve", {"code": code}, BAR)[0], 200)
        q = call("queue", tok=BAR)[1]
        self.assertNotIn(oid, [o["id"] for o in q["waiting"]])

    def test_desk_order_for_card(self):
        tok, code = student("0700000005")
        st, r = call("orders", {"size": "s", "code": code}, BAR)
        self.assertEqual((st, r["order"]["via"]), (200, "desk"))
        self.assertEqual(call("me", tok=tok)[1]["user"]["points"], 0)
        call(f"orders/{r['order']['id']}/serve", {"code": code, "pay": "cash"}, BAR)
        self.assertEqual(call("me", tok=tok)[1]["user"]["points"], 10)

    def test_free_order_spends_points(self):
        tok, code = student("0700000006")
        for _ in range(6):
            call(f"card/{code}/add", {"size": "s"}, BAR)
        st, r = call("orders", {"size": "l", "free": True}, tok)
        self.assertEqual((st, r["order"]["status"], r["order"]["amount"]), (200, "paid", 0))
        self.assertEqual(call("me", tok=tok)[1]["user"]["points"], 0)
        self.assertEqual(call("orders", {"size": "l", "free": True}, tok)[0], 409)
        st, rr = call(f"orders/{r['order']['id']}/serve", {"code": code}, BAR)  # за баллы — без отметки оплаты
        self.assertEqual((st, rr["order"]["status"], rr["delta"]), (200, "served", 0))
        self.assertEqual(call("me", tok=tok)[1]["user"]["points"], 0)

    def test_admin_students(self):
        tok, code = student("0700000021")
        r = call("admin/students?q=0700000021", tok=BOSS)[1]
        self.assertEqual([x["code"] for x in r["students"]], [code])
        self.assertEqual(call("admin/students", tok=BAR)[0], 403)
        sid = r["students"][0]["id"]
        pw = call(f"admin/students/{sid}/reset", {}, BOSS)[1]["password"]
        self.assertEqual(call("me", tok=tok)[0], 401)  # старые сессии закрыты
        self.assertEqual(call("login", {"phone": "0700000021", "password": pw})[0], 200)

    def test_limits_and_hardening(self):
        self.assertEqual(call("register", {"name": "Бот Ботов", "group": "X", "phone": "0700000031",
                                           "password": "secret1", "website": "spam"})[0], 400)  # ловушка для ботов
        self.assertEqual(call("login", raw=b"x" * 30_000)[0], 413)
        tok, _ = student("0700000032")
        codes = [call("me", tok=tok)[0] for _ in range(40)]
        self.assertIn(429, codes)  # одна сессия не может засыпать сервер запросами
        S.LIM_SESSION.b.clear()
        for _ in range(11):
            call("login", {"phone": "0700000032", "password": "wrong-pass"})
        self.assertEqual(call("login", {"phone": "0700000032", "password": "secret1"})[0], 429)  # перебор пароля
        S.LIM_LOGIN_ACC.b.clear()

    def test_admin_only(self):
        self.assertEqual(call("admin/stats", tok=BAR)[0], 403)
        st, r = call("admin/stats?days=7&test=1", tok=BOSS)
        self.assertEqual(st, 200)
        self.assertEqual(len(r["series"]), 7)

    def test_admin_staff_and_settings(self):
        st, r = call("admin/staff", {"login": "aigerim", "name": "Айгерим"}, BOSS)
        self.assertEqual(st, 200)
        self.assertEqual(call("login", {"phone": "aigerim", "password": r["password"]})[0], 200)
        sid = [s for s in call("admin/staff", tok=BOSS)[1]["staff"] if s["login"] == "aigerim"][0]["id"]
        call(f"admin/staff/{sid}/toggle", {}, BOSS)
        self.assertEqual(call("login", {"phone": "aigerim", "password": r["password"]})[0], 403)
        self.assertEqual(call("admin/settings", {"price_s": 150, "price_l": 180, "per_cup": 10, "free_cost": 65}, BOSS)[0], 400)
        st, r = call("admin/settings", {"price_s": 150, "price_l": 180, "per_cup": 10, "free_cost": 60}, BOSS)
        self.assertEqual((st, r["prices"]["s"]), (200, 150))
        call("admin/settings", {"price_s": 140, "price_l": 170, "per_cup": 10, "free_cost": 60}, BOSS)


class Hardening(unittest.TestCase):
    def test_slow_body_is_cut(self):
        """Тело по байту раз в секунду: сервер рвёт соединение по общему сроку, а не ждёт вечно."""
        import socket
        S.BODY_DEADLINE, saved = 1.5, S.BODY_DEADLINE
        try:
            c = socket.create_connection(("127.0.0.1", HTTPD.server_address[1]))
            c.sendall(b"POST /api/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 2000\r\n\r\n")
            t0 = time.time()
            c.settimeout(0.6)
            got = b""
            while time.time() - t0 < 6 and not got:
                try:
                    c.sendall(b"{")
                    got = c.recv(200)
                except socket.timeout:
                    pass
                except OSError:
                    break
            self.assertLess(time.time() - t0, 5)
            self.assertIn(b"408", got)
            c.close()
        finally:
            S.BODY_DEADLINE = saved

    def test_chunked_rejected(self):
        st, _ = call("login", raw=b"5\r\nhello\r\n0\r\n\r\n", headers={"Transfer-Encoding": "chunked"})
        self.assertIn(st, (400, 411))

    def test_fake_token_counts_to_ip_limit(self):
        S.LIM_IP.b.clear()
        codes = [call("me", tok="x" * 40)[0] for _ in range(200)]
        self.assertIn(429, codes)
        S.LIM_IP.b.clear()

    def test_staff_login_refused_from_pages(self):
        st, r = call("login", {"phone": "bar", "password": "barpass1"}, headers={"Origin": "https://seinokojii.github.io"})
        self.assertEqual(st, 403)

    def test_barista_cannot_undo_redeem_or_order(self):
        tok, code = student("0700000041")
        for _ in range(6):
            call(f"card/{code}/add", {"size": "s"}, BAR)
        op = call(f"card/{code}/redeem", {"size": "s"}, BAR)[1]["op"]
        self.assertEqual(call(f"op/{op['id']}/undo", {}, BAR)[0], 409)  # бесплатный кофе не «вернуть» бариста
        oid = call("orders", {"size": "l"}, tok)[1]["order"]["id"]
        call(f"orders/{oid}/serve", {"code": code, "pay": "cash"}, BAR)
        ops = call("today", tok=BAR)[1]["ops"]
        linked = [o for o in ops if o["name"] == "Тест Студент" and o["kind"] == "add" and not o["canUndo"]]
        self.assertTrue(linked)
        self.assertEqual(call(f"op/{linked[0]['id']}/undo", {}, BAR)[0], 409)  # продажа по заказу — только админ
        self.assertEqual(call(f"op/{op['id']}/undo", {}, BOSS)[0], 200)


class GoPayHook(unittest.TestCase):
    def setUp(self):
        S.GOPAY_HOOK_SECRET = "hooksecret"

    def tearDown(self):
        S.GOPAY_HOOK_SECRET = ""

    def make_pending(self):
        tok, _ = student(f"07001{int(time.time() * 1000) % 100000:05d}")
        with S.db() as con:
            uid = con.execute("SELECT id FROM users ORDER BY id DESC LIMIT 1").fetchone()["id"]
            now = int(time.time())
            cur = con.execute("""INSERT INTO orders(user_id,size,amount,provider,ext_id,created,expires)
                                 VALUES(?,?,?,?,?,?,?)""", (uid, "s", 140, "gopay", f"MC-T-{now}-{uid}", now, now + 900))
            return tok, cur.lastrowid, f"MC-T-{now}-{uid}"

    def hook(self, payload, secret="hooksecret"):
        body = json.dumps(payload, ensure_ascii=False)
        nonce = "abc123"
        return call("pay/gopay", raw=body.encode(), headers={
            "GoPay-Nonce": nonce, "GoPay-Signature": S.gopay_sign(secret, nonce, body)})

    def test_signed_event_marks_paid_once(self):
        tok, oid, ext = self.make_pending()
        ev = {"event": "payment.committed", "data": {"order_id": ext, "status": "COMMITTED", "amount": "140.00"}}
        self.assertEqual(self.hook(ev)[0], 200)
        self.assertEqual(self.hook(ev)[0], 200)  # повтор доставки
        me = call("me", tok=tok)[1]
        self.assertEqual(me["user"]["points"], 0)  # баллы — только при выдаче
        self.assertEqual(me["order"]["status"], "paid")
        self.assertEqual(call("admin/stats?days=1&test=1", tok=BOSS)[1]["qr"] >= 140, True)  # деньги в выручке сразу

    def test_late_payment_of_closed_order_is_kept(self):
        tok, oid, ext = self.make_pending()
        with S.db() as con:
            con.execute("UPDATE orders SET status='expired' WHERE id=?", (oid,))
        ev = {"event": "payment.committed", "data": {"order_id": ext, "status": "COMMITTED", "amount": "140.00"}}
        self.assertEqual(self.hook(ev)[0], 200)
        self.assertEqual(call("me", tok=tok)[1]["order"]["status"], "paid")  # деньги не пропали

    def test_amount_must_match_exactly(self):
        tok, oid, ext = self.make_pending()
        ev = {"event": "payment.committed", "data": {"order_id": ext, "status": "COMMITTED", "amount": "140.99"}}
        self.assertEqual(self.hook(ev)[0], 409)

    def test_bad_signature_rejected(self):
        tok, oid, ext = self.make_pending()
        ev = {"event": "payment.committed", "data": {"order_id": ext, "status": "COMMITTED", "amount": "140.00"}}
        self.assertEqual(self.hook(ev, secret="wrong")[0], 401)
        self.assertEqual(call("me", tok=tok)[1]["user"]["points"], 0)

    def test_wrong_amount_rejected(self):
        tok, oid, ext = self.make_pending()
        ev = {"event": "payment.committed", "data": {"order_id": ext, "status": "COMMITTED", "amount": "1.00"}}
        self.assertEqual(self.hook(ev)[0], 409)
        self.assertEqual(call("me", tok=tok)[1]["user"]["points"], 0)

    def test_legacy_callback_format(self):
        tok, oid, ext = self.make_pending()
        self.assertEqual(self.hook({"order_id": ext, "status": "COMMITTED", "amount": "140.00"})[0], 200)
        self.assertEqual(call("me", tok=tok)[1]["order"]["status"], "paid")


class GoPayCreate(unittest.TestCase):
    """Боевой путь создания платежа: ответ GoPay подменён, сеть не нужна."""
    def setUp(self):
        self.saved = (S.PAY_MODE, S.gopay_call)
        S.PAY_MODE = "gopay"
        self.calls = []
        def fake(path, data):
            self.calls.append((path, data))
            if path == "/v1/payments":
                return {"payment_id": "p1", "status": "CREATED", "qr_data": "https://pay.payqr.kg#0002",
                        "checkout_url": "https://pay.gopay.kg/p/p1/",
                        "app_links": {"mbank": "mbank://payment?x=1", "evil": "javascript:alert(1)"}}
            return {"status": "COMMITTED", "amount": "140.00"}
        S.gopay_call = fake

    def tearDown(self):
        S.PAY_MODE, S.gopay_call = self.saved

    def test_create_and_query_fallback(self):
        tok, _ = student("0700000099")
        st, r = call("orders", {"size": "s"}, tok)
        self.assertEqual(st, 200)
        o = r["order"]
        self.assertEqual(self.calls[0][1]["amount"], "140.00")
        self.assertEqual(o["appLinks"], {"mbank": "mbank://payment?x=1"})  # javascript: отброшен
        self.assertEqual(o["checkoutUrl"], "https://pay.gopay.kg/p/p1/")
        with S.db() as con:  # «прошло» 6 секунд — сервер сам спросит статус у GoPay
            con.execute("UPDATE orders SET created=created-6 WHERE id=?", (o["id"],))
        st, r = call(f"orders/{o['id']}", tok=tok)
        self.assertEqual((r["order"]["status"], r["points"]), ("paid", 0))
        self.assertEqual(self.calls[-1][0], "/v1/payments/query")


if __name__ == "__main__":
    unittest.main()
