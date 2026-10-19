"""Проверка API на временной базе: python3 -m unittest discover -s tests"""
import json, os, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ.update(DB_PATH=str(Path(TMP) / "t.db"), PAY_MODE="demo", DEMO_DELAY="0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server as S  # noqa: E402

S.init_db()
HTTPD = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
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
    def test_demo_order_pays_and_credits_points(self):
        tok, _ = student("0700000001")
        st, r = call("orders", {"size": "l"}, tok)
        self.assertEqual(st, 200)
        o = r["order"]
        self.assertEqual((o["status"], o["amount"]), ("pending", 170))
        st, r = call(f"orders/{o['id']}", tok=tok)
        self.assertEqual(r["order"]["status"], "paid")
        self.assertEqual(r["points"], 10)
        self.assertTrue(r["order"]["num"] >= 1)
        # Повторный опрос не начисляет второй раз.
        call(f"orders/{o['id']}", tok=tok)
        self.assertEqual(call("me", tok=tok)[1]["user"]["points"], 10)

    def test_other_student_cannot_see_order(self):
        tok, _ = student("0700000002")
        tok2, _ = student("0700000003")
        oid = call("orders", {"size": "s"}, tok)[1]["order"]["id"]
        self.assertEqual(call(f"orders/{oid}", tok=tok2)[0], 404)

    def test_queue_and_serve(self):
        tok, _ = student("0700000004")
        oid = call("orders", {"size": "s"}, tok)[1]["order"]["id"]
        call(f"orders/{oid}", tok=tok)
        q = call("queue", tok=BAR)[1]
        self.assertIn(oid, [o["id"] for o in q["waiting"]])
        self.assertEqual(call(f"orders/{oid}/serve", {}, BAR)[0], 200)
        q = call("queue", tok=BAR)[1]
        self.assertNotIn(oid, [o["id"] for o in q["waiting"]])
        self.assertEqual(call(f"orders/{oid}/serve", {}, tok)[0], 403)  # ученик не выдаёт

    def test_desk_order_for_card(self):
        tok, code = student("0700000005")
        st, r = call("orders", {"size": "s", "code": code}, BAR)
        self.assertEqual((st, r["order"]["via"]), (200, "desk"))
        call(f"orders/{r['order']['id']}", tok=BAR)
        self.assertEqual(call("me", tok=tok)[1]["user"]["points"], 10)

    def test_free_order_spends_points(self):
        tok, code = student("0700000006")
        for _ in range(6):
            call(f"card/{code}/add", {"size": "s"}, BAR)
        st, r = call("orders", {"size": "l", "free": True}, tok)
        self.assertEqual((st, r["order"]["status"], r["order"]["amount"]), (200, "paid", 0))
        self.assertEqual(call("me", tok=tok)[1]["user"]["points"], 0)
        self.assertEqual(call("orders", {"size": "l", "free": True}, tok)[0], 409)

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
        self.assertEqual(me["user"]["points"], 10)
        self.assertEqual(me["order"]["status"], "paid")

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
        self.assertEqual(call("me", tok=tok)[1]["user"]["points"], 10)


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
        self.assertEqual((r["order"]["status"], r["points"]), ("paid", 10))
        self.assertEqual(self.calls[-1][0], "/v1/payments/query")


if __name__ == "__main__":
    unittest.main()
