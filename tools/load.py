#!/usr/bin/env python3
"""Нагрузочная проверка: N одновременных учеников + бариста, держим соединения открытыми.

    python3 tools/load.py http://127.0.0.1:8936 tokens.txt --users 500 --seconds 30 --staff <токен>

Каждый ученик ведёт себя как приложение: опрашивает карту раз в 5 с, иногда заказывает кофе и
опрашивает заказ раз в 2 с, пока бариста не выдаст. Бариста (несколько) опрашивают очередь, сканируют QR
и выдают заказы. Печатает задержки p50/p95/p99, число запросов в секунду и сколько заказов выдано.
"""
import argparse, asyncio, json, random, time
from urllib.parse import urlsplit


class Conn:
    def __init__(self, host, port):
        self.host, self.port, self.r, self.w = host, port, None, None

    async def req(self, method, path, tok=None, body=None):
        if self.w is None:
            self.r, self.w = await asyncio.open_connection(self.host, self.port)
        data = json.dumps(body).encode() if body is not None else b""
        head = [f"{method} {path} HTTP/1.1", f"Host: {self.host}", "Connection: keep-alive"]
        if tok:
            head.append(f"Authorization: Bearer {tok}")
        if body is not None:
            head += ["Content-Type: application/json", f"Content-Length: {len(data)}"]
        self.w.write(("\r\n".join(head) + "\r\n\r\n").encode() + data)
        await self.w.drain()
        status_line = await self.r.readline()
        if not status_line:
            raise ConnectionError("closed")
        status = int(status_line.split()[1])
        length, close = 0, status_line.startswith(b"HTTP/1.0")
        while True:
            line = await self.r.readline()
            if line in (b"\r\n", b""):
                break
            k, _, v = line.decode().partition(":")
            if k.lower() == "content-length":
                length = int(v)
            if k.lower() == "connection":
                close = v.strip().lower() != "keep-alive"
        payload = await self.r.readexactly(length) if length else b""
        if close:
            self.w.close()
            self.w = None
        return status, payload

    def close(self):
        if self.w:
            self.w.close()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base"); ap.add_argument("tokens")
    ap.add_argument("--users", type=int, default=500); ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--staff", default="", help="токены бариста через запятую")
    ap.add_argument("--think", type=float, default=5.0, help="пауза между опросами карты, с (в приложении — 5)")
    ap.add_argument("--order", type=float, default=0.15, help="доля опросов, после которых ученик заказывает кофе")
    a = ap.parse_args()
    u = urlsplit(a.base)
    toks = [l.split() for l in open(a.tokens) if l.strip()]
    random.shuffle(toks)
    lat, codes, errors = [], {}, []
    stop = time.monotonic() + a.seconds

    async def timed(c, *args, **kw):
        t0 = time.perf_counter()
        try:
            st, body = await c.req(*args, **kw)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e)); c.w = None
            return None, b""
        lat.append(time.perf_counter() - t0)
        codes[st] = codes.get(st, 0) + 1
        return st, body

    pending = []  # (заказ, код карты) — бариста берёт отсюда, как будто ученик подошёл к кассе

    async def student(tok, code):
        c = Conn(u.hostname, u.port or 80)
        await asyncio.sleep(random.random() * a.think)
        while time.monotonic() < stop:
            await timed(c, "GET", "/api/me", tok)                      # приложение опрашивает карту
            if random.random() < a.order:
                st, body = await timed(c, "POST", "/api/orders", tok, {"size": random.choice("sl")})
                if st == 200:
                    oid = json.loads(body)["order"]["id"]
                    pending.append((oid, code))
                    for _ in range(15):                                   # шторка опрашивает заказ раз в 2 с
                        await asyncio.sleep(2)
                        st, body = await timed(c, "GET", f"/api/orders/{oid}", tok)
                        if st == 200 and json.loads(body)["order"]["status"] == "served":
                            break
            await asyncio.sleep(a.think * (0.6 + random.random() * .8))
        c.close()

    async def barista(tok):
        c = Conn(u.hostname, u.port or 80)
        while time.monotonic() < stop:
            await timed(c, "GET", "/api/queue", tok)
            for _ in range(4):
                if not pending:
                    break
                oid, code = pending.pop(0)
                await timed(c, "POST", "/api/orders/scan", tok, {"qr": "MUKRCOFFEE:" + code})
                st, _ = await timed(c, "POST", f"/api/orders/{oid}/serve", tok, {"code": code, "pay": random.choice(["cash", "card"])})
                served[0] += st == 200
                await asyncio.sleep(0.4)                                  # бариста отдаёт стакан
            await asyncio.sleep(1)
        c.close()

    served = [0]
    t0 = time.monotonic()
    jobs = [student(t[0], t[1]) for t in toks[:a.users]] + [barista(t) for t in a.staff.split(",") if t]
    await asyncio.gather(*jobs)
    dur = time.monotonic() - t0
    lat.sort()
    q = lambda p: lat[min(len(lat) - 1, int(len(lat) * p))] * 1000 if lat else 0
    print(f"пользователей: {a.users}, {dur:.0f} с, запросов: {len(lat)} ({len(lat) / dur:.0f}/с)")
    print(f"задержка, мс: p50 {q(.5):.1f} · p95 {q(.95):.1f} · p99 {q(.99):.1f} · макс {q(1):.1f}")
    print("ответы:", dict(sorted(codes.items())), "· обрывов:", len(errors), "· выдано заказов:", served[0])
    if errors:
        print("  пример:", errors[0])


asyncio.run(main())
