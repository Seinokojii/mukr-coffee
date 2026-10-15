/* МУКР Кофе — бонусная карта. Без сборки: один файл, рисует экраны в #app. */
(() => {
  "use strict";

  // Страницы на GitHub Pages ходят в API на сервере; на самом сервере — в тот же адрес.
  const API = location.hostname.endsWith("github.io") ? "https://mukrcoffee.62-113-109-193.sslip.io" : "";
  const QR_PREFIX = "MUKRCOFFEE:";
  let RULES = { perCup: 10, freeCost: 60, prices: { s: 140, l: 170 } };
  const SLOTS = () => Math.round(RULES.freeCost / RULES.perCup);

  const $app = document.getElementById("app");
  const store = {
    get tok() { try { return localStorage.getItem("mc.tok") || ""; } catch { return ""; } },
    set tok(v) { try { v ? localStorage.setItem("mc.tok", v) : localStorage.removeItem("mc.tok"); } catch {} },
  };
  let state = { user: null, me: null, guest: null, today: null, lastPts: null };
  let pollTimer = null;

  // ---------- иконки (Lucide) ----------
  const P = {
    coffee: '<path d="M10 2v2M14 2v2M6 2v2"/><path d="M16 8a1 1 0 0 1 1 1v8a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V9a1 1 0 0 1 1-1h14a4 4 0 1 1 0 8h-1"/>',
    out: '<path d="m16 17 5-5-5-5"/><path d="M21 12H9"/><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>',
    camera: '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/>',
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    max: '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/>',
    plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
    gift: '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13"/><path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/><path d="M7.5 8a2.5 2.5 0 0 1 0-5C9.5 3 12 8 12 8s2.5-5 4.5-5a2.5 2.5 0 0 1 0 5"/>',
  };
  const ico = (n, s = 20) => `<svg class="ico" width="${s}" height="${s}" viewBox="0 0 24 24" aria-hidden="true">${P[n]}</svg>`;

  // Оттиск штампа: кольцо + стакан + надпись по кругу. Цвет берёт от currentColor.
  let stampId = 0;
  const stamp = () => {
    const id = "arc" + (++stampId);
    return `<svg class="st" viewBox="0 0 100 100" aria-hidden="true">
      <defs><path id="${id}" d="M50 50 m-35 0 a35 35 0 1 1 70 0 a35 35 0 1 1 -70 0"/></defs>
      <circle cx="50" cy="50" r="46" fill="none" stroke="currentColor" stroke-width="4"/>
      <circle cx="50" cy="50" r="27" fill="none" stroke="currentColor" stroke-width="2"/>
      <text font-family="JetBrains Mono, monospace" font-size="9.5" font-weight="700" letter-spacing="2.6" fill="currentColor">
        <textPath href="#${id}">МУКР · КОФЕ · МУКР · КОФЕ ·</textPath></text>
      <g transform="translate(36 35) scale(1.18)" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">${P.coffee}</g>
    </svg>`;
  };
  const brandMark = () => `<svg viewBox="0 0 64 64" aria-hidden="true"><rect width="64" height="64" rx="14" fill="#1E1915"/><circle cx="32" cy="32" r="21" fill="none" stroke="#E0344F" stroke-width="3"/><g fill="none" stroke="#E0344F" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" transform="translate(18 18) scale(1.17)">${P.coffee}</g></svg>`;

  // ---------- помощники ----------
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const plural = (n, one, few, many) => {
    const a = Math.abs(n) % 100, b = a % 10;
    return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many;
  };
  const pts = n => `${n} ${plural(n, "балл", "балла", "баллов")}`;
  const cups = n => `${n} ${plural(n, "стакан", "стакана", "стаканов")}`;
  const size = s => (s === "l" ? "Большой" : "Маленький");
  const fmtCode = c => (c || "").replace(/(.{4})(.{4})/, "$1-$2");
  const fmtTime = ts => {
    const d = new Date(ts * 1000), now = new Date();
    const t = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    if (d.toDateString() === now.toDateString()) return "сегодня, " + t;
    return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" }) + ", " + t;
  };
  const rot = i => [-9, 6, -3, 11, -7, 4][i % 6] + "deg";

  let toastT;
  function toast(msg, bad) {
    const t = document.getElementById("toast");
    t.textContent = msg; t.className = "show" + (bad ? " bad" : "");
    clearTimeout(toastT); toastT = setTimeout(() => (t.className = bad ? "bad" : ""), 2800);
  }

  async function api(path, body) {
    const opt = { headers: {} };
    if (store.tok) opt.headers.Authorization = "Bearer " + store.tok;
    if (body !== undefined) { opt.method = "POST"; opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    let r;
    try { r = await fetch(API + "/api/" + path, opt); }
    catch { throw new Error("Нет связи с сервером. Проверьте интернет"); }
    const data = await r.json().catch(() => ({}));
    if (r.status === 401 && store.tok && path !== "login") { store.tok = ""; state.user = null; render(); }
    if (!r.ok) throw new Error(data.error || "Ошибка " + r.status);
    return data;
  }

  // ---------- карточка со штампами ----------
  function stampCard(points, { row = false, freshFrom = null, demo = false, desk = false } = {}) {
    const n = SLOTS(), filled = Math.min(n, Math.floor(points / RULES.perCup));
    const ready = points >= RULES.freeCost;
    let slots = "";
    for (let i = 0; i < n; i++) {
      const on = i < filled;
      const fresh = on && freshFrom !== null && i >= freshFrom;
      slots += on
        ? `<div class="slot on${fresh ? " fresh" : ""}" style="--rot:${rot(i)}">${stamp()}</div>`
        : `<div class="slot"><span class="n">${(i + 1) * RULES.perCup}</span></div>`;
    }
    const left = Math.ceil((RULES.freeCost - points) / RULES.perCup);
    const extra = ready && points > RULES.freeCost ? ` На карте ещё ${pts(points - RULES.freeCost)} сверху.` : "";
    const foot = ready
      ? `<p class="card-foot ready">${desk ? "Можно выдать бесплатный кофе." : "Бесплатный кофе готов — скажите бариста."}${extra}</p>`
      : demo ? "" : `<p class="card-foot">Ещё ${cups(left)} — и следующий кофе бесплатно.</p>`;
    return `<div class="stamps${row ? " row" : ""}">${slots}</div>${foot}`;
  }

  // ---------- гость: главная + вход ----------
  function viewLanding() {
    const p = RULES.prices;
    $app.innerHTML = `
      <header class="top">
        <a class="brand" href="#">${brandMark()}<span><b>МУКР Кофе</b><small>БОНУСНАЯ КАРТА</small></span></a>
        <button class="ghost" data-go="login">Войти</button>
      </header>
      <section class="hero">
        <div>
          <p class="kicker">Кофейня МУКР</p>
          <h1>Седьмой кофе — <em>за счёт кофейни</em></h1>
          <p class="lead">${RULES.perCup} баллов за каждый стакан. Накопили ${RULES.freeCost} — следующий кофе бесплатно. Карта всегда в телефоне.</p>
          <div class="cta-row">
            <button class="btn cherry" data-go="register">Завести карту</button>
            <button class="btn light" data-go="login">У меня уже есть</button>
          </div>
          <div class="prices">
            <div><b>${p.s} <small>сом</small></b>Маленький</div>
            <div><b>${p.l} <small>сом</small></b>Большой</div>
            <div><b>0 <small>сом</small></b>Седьмой</div>
          </div>
        </div>
        <div class="card hero-card" aria-label="Пример карты: четыре штампа из шести">
          <div class="card-head"><span class="name">Пример карты</span><span>${RULES.perCup} за стакан</span></div>
          <div class="pts" style="margin-bottom:16px">40<small>баллов</small></div>
          ${stampCard(40, { demo: true })}
        </div>
      </section>
      <section class="how" aria-label="Как это работает">
        <div class="how-in">
          <div class="step"><i>ШАГ 1</i><b>Заведите карту</b><p>Имя, группа и телефон. Сразу появится ваш личный QR-код.</p></div>
          <div class="step"><i>ШАГ 2</i><b>Покажите QR на кассе</b><p>Бариста сканирует код — за каждый стакан на карту падает ${RULES.perCup} баллов, любого размера.</p></div>
          <div class="step"><i>ШАГ 3</i><b>${RULES.freeCost} баллов — кофе бесплатно</b><p>Маленький или большой, на выбор. Баллы списываются, карта начинается заново.</p></div>
        </div>
      </section>
      <section class="auth" id="auth">
        <div>
          <h2>Ваша карта</h2>
          <p>Баллы хранятся на сервере кофейни, а не в телефоне: сменили телефон — войдите с тем же номером, всё на месте.</p>
        </div>
        <div class="card flat">${authForm("register")}</div>
      </section>
      <p class="foot">Кофейня Международного университета Кыргызской Республики</p>`;
    bindAuth();
  }

  function authForm(mode) {
    const reg = mode === "register";
    return `
      <div class="tabs" role="tablist">
        <button role="tab" aria-selected="${reg}" data-tab="register">Регистрация</button>
        <button role="tab" aria-selected="${!reg}" data-tab="login">Вход</button>
      </div>
      <form class="form" data-mode="${mode}" novalidate>
        ${reg ? `<div class="two">
          <div class="field"><label for="f-name">Имя и фамилия</label><input id="f-name" name="name" autocomplete="name" required></div>
          <div class="field"><label for="f-group">Группа</label><input id="f-group" name="group" placeholder="ПИ-2" required></div>
        </div>` : ""}
        <div class="field"><label for="f-phone">Телефон</label>
          <input id="f-phone" name="phone" type="${reg ? "tel" : "text"}" inputmode="${reg ? "tel" : "text"}" autocomplete="username" placeholder="0700 123 456" required>
          ${reg ? "" : '<div class="hint">Бариста входит по своему логину.</div>'}</div>
        <div class="field"><label for="f-pass">Пароль</label>
          <input id="f-pass" name="password" type="password" autocomplete="${reg ? "new-password" : "current-password"}" minlength="6" required>
          ${reg ? '<div class="hint">Не короче 6 символов.</div>' : ""}</div>
        <p class="err" id="f-err"></p>
        <button class="btn wide" type="submit">${reg ? "Завести карту" : "Войти"}</button>
      </form>`;
  }

  function bindAuth(focus) {
    const host = $app.querySelector(".auth .card");
    $app.querySelectorAll("[data-go]").forEach(b => b.onclick = () => {
      host.innerHTML = authForm(b.dataset.go); bindAuth(true);
      document.getElementById("auth").scrollIntoView({ behavior: "smooth" });
    });
    host.querySelectorAll("[data-tab]").forEach(b => b.onclick = () => { host.innerHTML = authForm(b.dataset.tab); bindAuth(true); });
    const f = host.querySelector("form");
    if (focus) setTimeout(() => f.querySelector("input").focus({ preventScroll: true }), 350);
    f.onsubmit = async e => {
      e.preventDefault();
      const btn = f.querySelector("button[type=submit]"), err = f.querySelector(".err");
      const body = Object.fromEntries(new FormData(f));
      btn.disabled = true; err.textContent = "";
      try {
        const r = await api(f.dataset.mode, body);
        store.tok = r.token; state.user = r.user;
        toast(f.dataset.mode === "register" ? "Карта готова" : "С возвращением, " + r.user.name.split(" ")[0]);
        window.scrollTo(0, 0); render();
      } catch (x) { err.textContent = x.message; btn.disabled = false; }
    };
  }

  // ---------- ученик ----------
  function qrSvg(code, cls) {
    const q = qrcode(0, "M"); q.addData(QR_PREFIX + code); q.make();
    return q.createSvgTag({ cellSize: 4, margin: 0, scalable: true }).replace("<svg", `<svg class="${cls || ""}" role="img" aria-label="QR-код карты ${code}"`);
  }

  function headerUser(label) {
    return `<header class="top">
      <a class="brand" href="#">${brandMark()}<span><b>МУКР Кофе</b><small>${label}</small></span></a>
      <button class="iconbtn" id="logout" aria-label="Выйти">${ico("out")}</button>
    </header>`;
  }

  function viewStudent() {
    const u = state.user, me = state.me;
    const points = me ? me.user.points : u.points;
    const prev = state.lastPts;
    const freshFrom = prev !== null && points > prev ? Math.floor(prev / RULES.perCup) : null;
    state.lastPts = points;
    const hist = me ? me.history : [];
    $app.innerHTML = `
      ${headerUser("БОНУСНАЯ КАРТА")}
      <div class="shell">
        <div class="hello"><h1>${esc(u.name.split(" ")[0])}</h1><p>${esc(u.group)}</p></div>
        <section class="ticket" aria-label="Ваш QR-код">
          <button class="iconbtn grow" id="grow" aria-label="Открыть QR во весь экран">${ico("max")}</button>
          <button class="qr" id="qr" aria-label="Открыть QR во весь экран">${qrSvg(u.code)}</button>
          <div class="code">${fmtCode(u.code)}</div>
          <p class="cap">Покажите бариста при оплате. Если камера не берёт — продиктуйте код.</p>
        </section>
        <section class="card" aria-label="Баллы">
          <div class="card-head"><span>Баллы</span><span>${RULES.perCup} за стакан</span></div>
          <div class="pts" style="margin-bottom:16px">${points}<small>${plural(points, "балл", "балла", "баллов")}</small></div>
          ${stampCard(points, { row: true, freshFrom })}
        </section>
        ${me ? `<div class="facts">
          <div class="fact"><b>${me.cups}</b><span>${plural(me.cups, "стакан выпит", "стакана выпито", "стаканов выпито")} по карте</span></div>
          <div class="fact"><b>${me.free}</b><span>${plural(me.free, "бесплатный получен", "бесплатных получено", "бесплатных получено")}</span></div>
        </div>` : ""}
        <section class="hist">
          <h2>История</h2>
          ${hist.length ? `<ul class="list">${hist.map(o => `<li>
            <span class="dot${o.kind === "redeem" ? " free" : ""}">${ico(o.kind === "redeem" ? "gift" : "coffee", 18)}</span>
            <span class="what"><b>${o.kind === "redeem" ? "Бесплатный кофе" : "Кофе"} · ${size(o.size).toLowerCase()}</b><span>${fmtTime(o.ts)}</span></span>
            <span class="d ${o.delta > 0 ? "plus" : "minus"}">${o.delta > 0 ? "+" : "−"}${Math.abs(o.delta)}</span></li>`).join("")}</ul>`
          : `<div class="empty">Здесь появится каждый стакан. Первый штамп — на кассе.</div>`}
        </section>
      </div>`;
    document.getElementById("logout").onclick = logout;
    document.getElementById("qr").onclick = document.getElementById("grow").onclick = () => fullQr(u.code);
    if (freshFrom !== null) toast(`+${pts(points - prev)}. Спасибо за кофе!`);
  }

  function fullQr(code) {
    const d = document.createElement("div");
    d.className = "full"; d.setAttribute("role", "dialog"); d.setAttribute("aria-label", "QR-код");
    d.innerHTML = `<button class="iconbtn" aria-label="Закрыть">${ico("x", 26)}</button>
      <div class="box">${qrSvg(code, "q")}<div class="code">${fmtCode(code)}</div></div>`;
    const close = () => { d.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = e => e.key === "Escape" && close();
    d.onclick = close; document.addEventListener("keydown", onKey);
    document.body.appendChild(d); d.querySelector("button").focus();
  }

  async function refreshMe() {
    try {
      const me = await api("me");
      const changed = !state.me || me.user.points !== state.me.user.points || me.history.length !== state.me.history.length;
      state.me = me; state.user = me.user;
      if (changed && state.user.role === "student") viewStudent();
    } catch {}
  }

  // ---------- бариста ----------
  let cam = { stream: null, raf: 0 };

  function viewDesk() {
    const u = state.user;
    $app.innerHTML = `
      ${headerUser("КАССА · " + esc(u.name).toUpperCase())}
      <div class="desk">
        <div class="col">
          <section class="scan" aria-label="Сканер">
            <div class="viewport" id="vp">
              <div class="idle">${ico("camera", 32)}<span>Наведите камеру на QR ученика</span>
                <button class="btn cherry" id="camOn">Включить камеру</button></div>
            </div>
            <form class="manual" id="manual">
              <label class="sr" for="m-code">Код карты</label>
              <input id="m-code" placeholder="Код: XXXX-XXXX" autocomplete="off" autocapitalize="characters" spellcheck="false" maxlength="9">
              <button class="btn" type="submit">Найти</button>
            </form>
          </section>
          <section id="guest"></section>
        </div>
        <section class="panel" id="today"></section>
      </div>`;
    document.getElementById("logout").onclick = logout;
    document.getElementById("camOn").onclick = startCam;
    document.getElementById("manual").onsubmit = e => {
      e.preventDefault();
      const v = document.getElementById("m-code").value.replace(/[^a-z0-9]/gi, "");
      if (v.length < 8) return toast("В коде 8 знаков", true);
      lookup(v);
    };
    renderGuest(); loadToday();
  }

  async function startCam() {
    const vp = document.getElementById("vp");
    if (!navigator.mediaDevices?.getUserMedia) return toast("Браузер не даёт камеру — введите код вручную", true);
    try {
      cam.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
    } catch { return toast("Нет доступа к камере. Разрешите её в настройках браузера", true); }
    vp.innerHTML = `<video playsinline muted></video><div class="frame"></div>`;
    const v = vp.querySelector("video"); v.srcObject = cam.stream; await v.play().catch(() => {});
    const c = document.createElement("canvas"), ctx = c.getContext("2d", { willReadFrequently: true });
    let busy = false;
    const tick = () => {
      cam.raf = requestAnimationFrame(tick);
      if (busy || v.readyState < 2 || !window.jsQR) return;
      const w = 480, h = Math.round(v.videoHeight / v.videoWidth * w) || 360;
      c.width = w; c.height = h; ctx.drawImage(v, 0, 0, w, h);
      const r = jsQR(ctx.getImageData(0, 0, w, h).data, w, h, { inversionAttempts: "dontInvert" });
      if (r && r.data.startsWith(QR_PREFIX)) {
        busy = true; navigator.vibrate?.(60);
        lookup(r.data.slice(QR_PREFIX.length)).finally(() => setTimeout(() => (busy = false), 2500));
      }
    };
    tick();
  }
  function stopCam() {
    cancelAnimationFrame(cam.raf);
    cam.stream?.getTracks().forEach(t => t.stop()); cam.stream = null;
  }

  async function lookup(code) {
    try { state.guest = (await api("card/" + code)).card; renderGuest(); document.getElementById("guest").scrollIntoView({ behavior: "smooth", block: "nearest" }); }
    catch (x) { toast(x.message, true); }
  }

  function renderGuest(lastOp) {
    const g = state.guest, el = document.getElementById("guest");
    if (!el) return;
    if (!g) { el.innerHTML = ""; return; }
    const can = g.points >= RULES.freeCost, p = RULES.prices;
    el.innerHTML = `<div class="card flat guest">
      <div class="who"><div><b>${esc(g.name)}</b><span>${esc(g.group)} · ${fmtCode(g.code)}</span></div>
        <button class="iconbtn" id="gx" aria-label="Закрыть карту">${ico("x")}</button></div>
      <div class="pts">${g.points}<small>${plural(g.points, "балл", "балла", "баллов")}</small></div>
      ${stampCard(g.points, { row: true, desk: true, freshFrom: lastOp?.kind === "add" ? Math.floor((g.points - RULES.perCup) / RULES.perCup) : null })}
      <div class="acts">
        <button class="act" data-k="add" data-s="s"><b>Маленький</b><span>${p.s} сом</span><em>+${RULES.perCup}</em></button>
        <button class="act" data-k="add" data-s="l"><b>Большой</b><span>${p.l} сом</span><em>+${RULES.perCup}</em></button>
        <button class="act redeem" data-k="redeem" data-s="s" ${can ? "" : "disabled"}><b>Бесплатный маленький</b><em>−${RULES.freeCost}</em></button>
        <button class="act redeem" data-k="redeem" data-s="l" ${can ? "" : "disabled"}><b>Бесплатный большой</b><em>−${RULES.freeCost}</em></button>
      </div>
      ${can ? "" : `<p class="sub">Бесплатный кофе откроется на ${RULES.freeCost} баллах.</p>`}
    </div>`;
    document.getElementById("gx").onclick = () => { state.guest = null; renderGuest(); };
    el.querySelectorAll(".act").forEach(b => b.onclick = async () => {
      el.querySelectorAll(".act").forEach(x => (x.disabled = true));
      try {
        const r = await api(`card/${g.code}/${b.dataset.k}`, { size: b.dataset.s });
        state.guest = r.card; renderGuest(r.op);
        toast(r.op.kind === "add" ? `+${r.op.delta} — ${g.name.split(" ")[0]}` : `Бесплатный ${size(r.op.size).toLowerCase()} выдан`);
        loadToday();
      } catch (x) { toast(x.message, true); renderGuest(); }
    });
  }

  async function loadToday() {
    const el = document.getElementById("today");
    if (!el) return;
    try { state.today = await api("today"); } catch { return; }
    const t = state.today;
    el.innerHTML = `<h2>Сегодня</h2>
      <div class="sum">
        <div class="fact"><b>${t.sold}</b><span>${plural(t.sold, "стакан", "стакана", "стаканов")} по картам</span></div>
        <div class="fact"><b>${t.free}</b><span>бесплатных</span></div>
        <div class="fact"><b>${t.revenue}</b><span>сом по картам</span></div>
      </div>
      <h2 style="margin-top:18px">Операции</h2>
      ${t.ops.length ? `<ul class="list">${t.ops.map(o => `<li class="${o.undone ? "undone" : ""}">
        <span class="dot${o.kind === "redeem" ? " free" : ""}">${ico(o.kind === "redeem" ? "gift" : "coffee", 18)}</span>
        <span class="what"><b>${esc(o.name)}</b><span>${esc(o.group)} · ${size(o.size).toLowerCase()} · ${fmtTime(o.ts).replace("сегодня, ", "")}${o.undone ? " · отменено" : ""}</span></span>
        ${o.canUndo ? `<button class="undo" data-op="${o.id}" aria-label="Отменить операцию">${ico("undo", 16)}<span class="t">Отменить</span></button>` : ""}
        <span class="d ${o.delta > 0 ? "plus" : "minus"}">${o.delta > 0 ? "+" : "−"}${Math.abs(o.delta)}</span></li>`).join("")}</ul>`
      : `<div class="empty">Пока ни одного стакана. Отсканируйте первый QR.</div>`}`;
    el.querySelectorAll("[data-op]").forEach(b => b.onclick = async () => {
      b.disabled = true;
      try {
        const r = await api(`op/${b.dataset.op}/undo`, {});
        if (state.guest && state.guest.code === r.card.code) { state.guest = r.card; renderGuest(); }
        toast("Операция отменена"); loadToday();
      } catch (x) { toast(x.message, true); b.disabled = false; }
    });
  }

  // ---------- общее ----------
  async function logout() {
    stopCam(); clearInterval(pollTimer);
    try { await api("logout", {}); } catch {}
    store.tok = ""; state = { user: null, me: null, guest: null, today: null, lastPts: null };
    render();
  }

  function render() {
    stopCam(); clearInterval(pollTimer);
    if (!state.user) return viewLanding();
    if (state.user.role === "student") {
      viewStudent(); refreshMe();
      // Пока карта открыта, следим за баллами: бариста отсканировал — штамп появляется сам.
      pollTimer = setInterval(() => document.visibilityState === "visible" && refreshMe(), 4000);
    } else {
      viewDesk();
      pollTimer = setInterval(() => document.visibilityState === "visible" && loadToday(), 30000);
    }
  }

  async function boot() {
    $app.innerHTML = `<div class="loading">Загрузка…</div>`;
    api("rules").then(r => { RULES = r; }).catch(() => {});
    if (store.tok) {
      try { const me = await api("me"); state.user = me.user; state.me = me; } catch { }
    }
    render();
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state.user?.role === "student") refreshMe();
  });
  boot();
})();
