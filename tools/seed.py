#!/usr/bin/env python3
"""Заполнить ТЕСТОВУЮ базу учениками и сессиями для нагрузочной проверки.

    DB_PATH=/tmp/load.db python3 tools/seed.py 10000 tokens.txt

Пароль у всех — load-test (хеш считается один раз). Токены сессий пишутся в файл,
их берёт tools/load.py. На боевую базу не запускать.
"""
import hashlib, secrets, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server as S  # noqa: E402

n, out = int(sys.argv[1]), sys.argv[2]
if "coffee.db" in str(S.DB_PATH):
    sys.exit("Это похоже на боевую базу — укажите DB_PATH на временный файл")
S.init_db()
pw = S.hash_pw("load-test")
now = int(time.time())
toks = []
with S.db() as con:
    codes = {r[0] for r in con.execute("SELECT code FROM users")}
    for i in range(n):
        while True:
            c = "".join(secrets.choice(S.CODE_ALPHABET) for _ in range(8))
            if c not in codes:
                codes.add(c)
                break
        cur = con.execute("INSERT INTO users(role,login,name,grp,pass,code,created) VALUES('student',?,?,?,?,?,?)",
                          (f"99677{i:07d}", f"Ученик {i}", f"ГР-{i % 40}", pw, c, now))
        t = secrets.token_urlsafe(32)
        key = hashlib.sha256(t.encode()).hexdigest() if getattr(S, "HASH_TOKENS", False) else t
        con.execute("INSERT INTO sessions(token,user_id,created) VALUES(?,?,?)", (key, cur.lastrowid, now))
        toks.append(f"{t} {c}")
Path(out).write_text("\n".join(toks) + "\n")
print(f"учеников: {n}, токены → {out}")
