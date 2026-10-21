/* МУКР Кофе — бонусная карта. Без сборки: один файл, рисует экраны в #app. */
(() => {
  "use strict";

  // Карта живёт только на сервере кофейни: на github.io общий для всех проектов localStorage,
  // и токен кассы там мог бы прочитать чужой скрипт. Старая ссылка просто переводит сюда.
  const HOME = "https://mukrcoffee.62-113-109-193.sslip.io/";
  if (location.hostname.endsWith("github.io")) { location.replace(HOME + location.hash); return; }
  const API = "";
  const QR_PREFIX = "MUKRCOFFEE:";        // личный QR ученика — один на всё: и штамп, и выдача заказа
  const PICKUP = "MUKRCOFFEE-ORDER:";     // QR заказа из прошлых версий — касса его тоже понимает
  const MOTION = !matchMedia("(prefers-reduced-motion: reduce)").matches;
  let RULES = { perCup: 10, freeCost: 60, prices: { s: 140, l: 170 }, pay: "desk" };
  const SLOTS = () => Math.round(RULES.freeCost / RULES.perCup);
  const DESK = p => p === "desk" || p === "demo";

  const $app = document.getElementById("app");
  const ls = {
    get: k => { try { return localStorage.getItem(k) || ""; } catch { return ""; } },
    set: (k, v) => { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch {} },
  };
  const store = { get tok() { return ls.get("mc.tok"); }, set tok(v) { ls.set("mc.tok", v); } };
  const fresh = () => ({ user: null, me: null, guest: null, guestOrders: [], today: null, lastPts: null, lastOp: 0 });
  let state = fresh();
  let pollTimer = null;
  let viewId = 0; // меняется при смене экрана — останавливает петли старого экрана
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // ---------- иконки (Lucide) ----------
  const P = {
    coffee: '<path d="M10 2v2M14 2v2M6 2v2"/><path d="M16 8a1 1 0 0 1 1 1v8a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V9a1 1 0 0 1 1-1h14a4 4 0 1 1 0 8h-1"/>',
    out: '<path d="m16 17 5-5-5-5"/><path d="M21 12H9"/><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>',
    camera: '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/>',
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    gift: '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13"/><path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/><path d="M7.5 8a2.5 2.5 0 0 1 0-5C9.5 3 12 8 12 8s2.5-5 4.5-5a2.5 2.5 0 0 1 0 5"/>',
    userPlus: '<path d="M2 21a8 8 0 0 1 13.292-6"/><circle cx="10" cy="8" r="5"/><path d="M19 16v6"/><path d="M22 19h-6"/>',
    qr: '<rect width="5" height="5" x="3" y="3" rx="1"/><rect width="5" height="5" x="16" y="3" rx="1"/><rect width="5" height="5" x="3" y="16" rx="1"/><path d="M21 16h-3a2 2 0 0 0-2 2v3"/><path d="M21 21v.01"/><path d="M12 7v3a2 2 0 0 1-2 2H7"/><path d="M3 12h.01"/><path d="M12 3h.01"/><path d="M12 16v.01"/><path d="M16 12h1"/><path d="M21 12v.01"/><path d="M12 21v-1"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    cash: '<rect width="20" height="12" x="2" y="6" rx="2"/><circle cx="12" cy="12" r="2"/><path d="M6 12h.01M18 12h.01"/>',
    card: '<rect width="20" height="14" x="2" y="5" rx="2"/><path d="M2 10h20"/>',
    search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    flip: '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
    key: '<path d="m15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4"/><path d="m21 2-9.6 9.6"/><circle cx="7.5" cy="15.5" r="5.5"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
  };
  const ico = (n, s = 20) => `<svg class="ico" width="${s}" height="${s}" viewBox="0 0 24 24" aria-hidden="true">${P[n]}</svg>`;

  // Башня МУКР с эмблемы (шпиль, фонарь, барабан, арки), рамка 60×116.
  // Части снизу вверх: --i задаёт очередь, в которой они прорисовываются на первом экране.
  const TOWER = [
    "M15 108H45V113H15Z", "M20 83H40V108H20Z", "M18 80H42V83H18Z", "M24 61H36V80H24Z",
    "M22 58H38V61H22Z", "M26.5 42H33.5V58H26.5Z", "M27.6 39.5a2.4 2.4 0 1 0 4.8 0a2.4 2.4 0 1 0-4.8 0Z", "M30 2L31.8 37H28.2Z",
  ];
  const TOWER_CUTS = [ // арки внизу, окна барабана, прорезь фонаря
    "M22.6 108V97a2 2 0 0 1 4 0V108Z", "M28 108V97a2 2 0 0 1 4 0V108Z", "M33.4 108V97a2 2 0 0 1 4 0V108Z",
    "M26.4 76V69a1.4 1.4 0 0 1 2.8 0V76Z", "M30.8 76V69a1.4 1.4 0 0 1 2.8 0V76Z", "M29 54V47a1 1 0 0 1 2 0V54Z",
  ];
  const tower = (tf, body = "currentColor", cut = "none") =>
    `<g transform="${tf}"><g class="tw" fill="${body}">${TOWER.map((d, i) => `<path d="${d}" pathLength="1" style="--i:${i}"/>`).join("")}</g>` +
    `<g class="tw-cuts" fill="${cut}">${TOWER_CUTS.map(d => `<path d="${d}" pathLength="1"/>`).join("")}</g></g>`;

  // Печать на первом экране: кольцо с названием университета, чашка, пар и башня (анимация — в CSS, .seal).
  const sealSvg = () => `<svg viewBox="0 0 200 200" aria-hidden="true">
    <g class="ring-wrap">
      <circle class="edge" cx="100" cy="100" r="97"/>
      <g class="ring"><defs><path id="sealArc" d="M100 100m-84 0a84 84 0 1 1 168 0a84 84 0 1 1-168 0"/></defs>
        <text textLength="522" lengthAdjust="spacing"><textPath href="#sealArc">МЕЖДУНАРОДНЫЙ УНИВЕРСИТЕТ КЫРГЫЗСКОЙ РЕСПУБЛИКИ · КОФЕЙНЯ · 1993 ·</textPath></text></g>
      <circle class="disc" cx="100" cy="100" r="72"/>
    </g>
    ${[90, 100, 110].map(x => `<path class="steam" pathLength="1" d="M${x} 124c-7-12 7-20 0-30s7-20 0-30s7-20 0-28"/>`).join("")}
    ${tower("translate(76 30) scale(.8)")}
    <g class="cup"><path pathLength="1" d="M84 127H116V137a13 13 0 0 1-13 13H97a13 13 0 0 1-13-13Z"/><path pathLength="1" d="M116 130h3.5a5 5 0 0 1 0 10H116"/><path pathLength="1" d="M78 156H122"/></g>
  </svg>`;

  // Оттиск на карте — печать МУКР: кольцо, надпись по кругу, башня. Цвет чернил — currentColor, прорези — цвет бумаги.
  let stampId = 0;
  const stamp = (paper = "#FFFFFF") => {
    const id = "arc" + (++stampId);
    return `<svg class="stp" viewBox="0 0 100 100" aria-hidden="true">
      <defs><path id="${id}" d="M50 50 m-40 0 a40 40 0 1 1 80 0 a40 40 0 1 1 -80 0"/></defs>
      <circle cx="50" cy="50" r="47" fill="none" stroke="currentColor" stroke-width="3.5"/>
      <circle cx="50" cy="50" r="33" fill="none" stroke="currentColor" stroke-width="1.6"/>
      <text font-family="JetBrains Mono, monospace" font-size="8.6" font-weight="700" fill="currentColor" textLength="246" lengthAdjust="spacing">
        <textPath href="#${id}">МУКР · КОФЕ · 1993 · МУКР · КОФЕ · 1993 ·</textPath></text>
      ${tower("translate(36.2 23.4) scale(.46)", "currentColor", paper)}
    </svg>`;
  };
  const brandMark = () => `<svg viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="31" fill="#F1D3A1"/><circle cx="32" cy="32" r="26" fill="#16006F"/>${tower("translate(20.6 10.5) scale(.38)", "#fff", "#16006F")}</svg>`;
  // Резиновый штамп для сцены на главной: ручка цвета пенки, корпус, индиговая подушка.
  const toolSvg = `<svg viewBox="0 0 76 112" aria-hidden="true">
    <ellipse cx="38" cy="17" rx="17" ry="15" fill="#F1D3A1"/><ellipse cx="33" cy="12" rx="6" ry="4" fill="#FFF1D6"/>
    <rect x="31" y="28" width="14" height="30" rx="4" fill="#F1D3A1"/>
    <rect x="8" y="56" width="60" height="32" rx="8" fill="#0F0A3C"/><rect x="8" y="56" width="60" height="8" rx="4" fill="#2A2A9E"/>
    <rect x="12" y="88" width="52" height="14" rx="4" fill="#F1D3A1"/></svg>`;
  const beanSvg = c => `<svg viewBox="0 0 16 22" width="16" height="22"><ellipse cx="8" cy="11" rx="7" ry="10" fill="${c}"/><path d="M8 2c-3.5 5 3.5 13 0 18" stroke="rgba(0,0,0,.35)" stroke-width="1.6" fill="none"/></svg>`;

  // ---------- помощники ----------
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const plural = (n, one, few, many) => {
    const a = Math.abs(n) % 100, b = a % 10;
    return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many;
  };
  const pts = n => `${n} ${plural(n, "балл", "балла", "баллов")}`;
  const ptsWord = n => plural(n, "балл", "балла", "баллов");
  const cups = n => `${n} ${plural(n, "стакан", "стакана", "стаканов")}`;
  const size = s => (s === "l" ? "Большой" : "Маленький");
  const fmtCode = c => (c || "").replace(/(.{4})(.{4})/, "$1-$2");
  const fmtSom = n => Number(n || 0).toLocaleString("ru-RU").replace(/,/g, " ");
  const first = n => esc(String(n || "").split(" ")[0]);
  const fmtTime = ts => {
    const d = new Date(ts * 1000), now = new Date();
    const t = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    if (d.toDateString() === now.toDateString()) return "сегодня, " + t;
    return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" }) + ", " + t;
  };
  const ago = ts => { const m = Math.max(0, Math.floor((Date.now() / 1000 - ts) / 60)); return m < 1 ? "только что" : m + " мин назад"; };
  const rot = i => [-9, 6, -3, 11, -7, 4][i % 6] + "deg";

  let toastT;
  function toast(msg, bad) {
    const t = document.getElementById("toast");
    t.textContent = msg; t.className = "show" + (bad ? " bad" : "");
    clearTimeout(toastT); toastT = setTimeout(() => (t.className = bad ? "bad" : ""), 2800);
  }

  // Число в .pts плавно доезжает до нового значения.
  function countTo(el, from, to, ms = 700) {
    if (!el) return;
    const num = el.querySelector(".num"), word = el.querySelector("small");
    if (!MOTION || from === to || from == null) { num.textContent = to; word.textContent = ptsWord(to); return; }
    const t0 = performance.now();
    const step = now => {
      const k = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - k, 3);
      const v = Math.round(from + (to - from) * e);
      num.textContent = v; word.textContent = ptsWord(v);
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  const ptsHtml = n => `<div class="pts"><span class="num">${n}</span><small>${ptsWord(n)}</small></div>`;

  // Зёрна разлетаются из элемента — когда на карте набралось на бесплатный кофе.
  function beans(el) {
    if (!MOTION || !el) return;
    const r = el.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 3;
    const colors = ["#6B3F22", "#8A5530", "#F1D3A1", "#8F95D6"];
    for (let i = 0; i < 26; i++) {
      const b = document.createElement("div");
      b.className = "bean"; b.innerHTML = beanSvg(colors[i % colors.length]);
      document.body.appendChild(b);
      const a = Math.random() * Math.PI * 2, d = 120 + Math.random() * 180;
      const dx = Math.cos(a) * d, dy = Math.sin(a) * d - 120, r0 = Math.random() * 360;
      b.animate([
        { transform: `translate(${cx}px, ${cy}px) rotate(${r0}deg) scale(.4)`, opacity: 1 },
        { transform: `translate(${cx + dx * .7}px, ${cy + dy * .7}px) rotate(${r0 + 220}deg) scale(1)`, opacity: 1, offset: .55 },
        { transform: `translate(${cx + dx}px, ${cy + dy + 260}px) rotate(${r0 + 420}deg) scale(.8)`, opacity: 0 },
      ], { duration: 1500 + Math.random() * 600, easing: "cubic-bezier(.2,.7,.4,1)" }).onfinish = () => b.remove();
    }
  }

  // Волна от нажатия на кнопку кассы.
  function ripple(btn, e) {
    if (!MOTION || !btn) return;
    const r = btn.getBoundingClientRect(), s = Math.max(r.width, r.height) * 2.2, d = document.createElement("span");
    d.className = "rip"; d.style.cssText = `width:${s}px;height:${s}px;left:${(e?.clientX || r.left + r.width / 2) - r.left - s / 2}px;top:${(e?.clientY || r.top + r.height / 2) - r.top - s / 2}px`;
    btn.appendChild(d); setTimeout(() => d.remove(), 650);
  }

  // Проявление блоков при прокрутке.
  function reveal(root = $app) {
    const els = [...root.querySelectorAll(".rv, .step")];
    if (!MOTION) { els.forEach(e => e.classList.add("in")); return; }
    // Проверка по прокрутке, а не IntersectionObserver: тот пропускает блоки, через которые перескочили якорем.
    const check = () => {
      const h = innerHeight * .88;
      for (let i = els.length - 1; i >= 0; i--) if (els[i].getBoundingClientRect().top < h) { els[i].classList.add("in"); els.splice(i, 1); }
      if (!els.length) removeEventListener("scroll", onScroll);
    };
    let raf = 0;
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; check(); }); };
    addEventListener("scroll", onScroll, { passive: true });
    check();
  }

  async function api(path, body, tries = 0) {
    const opt = { headers: {} };
    if (store.tok) opt.headers.Authorization = "Bearer " + store.tok;
    if (body !== undefined) { opt.method = "POST"; opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    let r;
    try { r = await fetch(API + "/api/" + path, opt); }
    catch { throw new Error("Нет связи с сервером. Проверьте интернет"); }
    const data = await r.json().catch(() => ({}));
    // Вход и регистрация в час пик: сервер проверяет пароли по очереди и просит повторить — повторяем сами.
    if (r.status === 503 && (path === "login" || path === "register") && tries < 4) {
      await sleep(800 + Math.random() * 1200 * (tries + 1));
      return api(path, body, tries + 1);
    }
    if (r.status === 401 && store.tok && path !== "login") { store.tok = ""; state.user = null; render(); }
    if (!r.ok) throw new Error(data.error || (r.status === 503 ? "Сервер занят, попробуйте через минуту" : "Ошибка " + r.status));
    return data;
  }

  // ---------- оттиски ----------
  function slotsHtml(points, freshFrom = null, paper) {
    const n = SLOTS(), filled = Math.min(n, Math.floor(points / RULES.perCup));
    let s = "";
    for (let i = 0; i < n; i++) {
      const on = i < filled, isFresh = on && freshFrom !== null && i >= freshFrom;
      s += on
        ? `<div class="slot on${isFresh ? " fresh" : ""}" style="--rot:${rot(i)}">${stamp(paper)}</div>`
        : `<div class="slot"><span class="n">${(i + 1) * RULES.perCup}</span></div>`;
    }
    return s;
  }
  const leftText = (points, desk) => {
    if (points >= RULES.freeCost) {
      const extra = points > RULES.freeCost ? ` Сверху ещё ${pts(points - RULES.freeCost)}.` : "";
      return `<span class="ready">${desk ? "Можно выдать бесплатный кофе." : "Бесплатный кофе готов — закажите ниже."}${extra}</span>`;
    }
    return `Ещё <b>${cups(Math.ceil((RULES.freeCost - points) / RULES.perCup))}</b> — и следующий кофе бесплатно`;
  };

  // Карта-пропуск ученика: спереди баллы и оттиски, нажатие переворачивает — сзади личный QR.
  function passHtml(u, points, { prev = points, freshFrom = null, demo = false } = {}) {
    const ready = points >= RULES.freeCost;
    const front = `<div class="face front">
        <div class="p-top"><span class="p-brand">${brandMark()}<b>МУКР Кофе</b></span><span class="p-rate">+${RULES.perCup} за стакан</span></div>
        ${ptsHtml(prev)}
        <div class="stamps dark" ${demo ? 'id="demoSlots"' : ""}>${slotsHtml(points, freshFrom, "#1B0E7A")}</div>
        <div class="p-foot"><span class="p-name">${esc(u.name)}</span>${demo ? "" : `<span class="p-flip">${ico("flip", 15)}QR</span>`}</div>
      </div>`;
    if (demo) return `<div class="pass${ready ? " ready" : ""}" id="demo">${front}<div class="badge" id="badge">${ico("gift")}Кофе бесплатно</div></div>`;
    return `<button class="pass${ready ? " ready" : ""}" id="pass" type="button" aria-pressed="false" aria-label="Карта: ${pts(points)}. Нажмите, чтобы показать QR">
        <div class="pass-in">${front}
          <div class="face back">
            <div class="p-qr">${qrSvg(u.code)}</div>
            <div class="p-code">${fmtCode(u.code)}</div>
            <span class="p-hint">Нажмите, чтобы вернуть карту</span>
          </div></div>
      </button>`;
  }

  // ---------- гость: главная + вход ----------
  function viewLanding() {
    const p = RULES.prices;
    $app.innerHTML = `
      <header class="top">
        <a class="brand" href="#">${brandMark()}<span><b>МУКР Кофе</b><small>Бонусная карта</small></span></a>
        <button class="ghost" data-go="login">Войти</button>
      </header>
      <section class="hero intro">
        <div class="hero-txt">
          <div class="seal">${sealSvg()}</div>
          <h1><span class="ln"><span>Седьмой кофе&nbsp;—</span></span><span class="ln"><span><em>за&nbsp;счёт</em></span></span><span class="ln"><span><em>кофейни</em></span></span></h1>
          <p class="lead">${RULES.perCup} баллов за каждый стакан. Накопили ${RULES.freeCost}&nbsp;— следующий кофе бесплатно. Карта всегда в&nbsp;телефоне.</p>
          <div class="cta-row">
            <button class="btn" data-go="register">Завести карту</button>
            <button class="btn soft" data-go="login">У меня уже есть карта</button>
          </div>
          <div class="prices">
            <div><b>${p.s} <small>сом</small></b>Маленький</div>
            <div><b>${p.l} <small>сом</small></b>Большой</div>
            <div class="gift"><b>0 <small>сом</small></b>Седьмой</div>
          </div>
        </div>
        <div class="stage" id="stage" aria-label="Пример карты: бариста ставит оттиск за каждый стакан">
          <div class="tilt">${passHtml({ name: "Ваше имя" }, 0, { demo: true })}</div>
          <div class="tool" id="tool">${toolSvg}</div>
        </div>
      </section>
      <section class="how" aria-label="Как это работает">
        <div class="how-in">
          <h2 class="rv">Три шага до&nbsp;бесплатного кофе</h2>
          <div class="step" style="--d:0s"><span class="no">1</span><b>Заведите карту</b><p>Имя, группа и телефон — полминуты. Сразу появится личный QR.</p></div>
          <div class="step" style="--d:.15s"><span class="no">2</span><b>Закажите и покажите QR</b><p>Выберите кофе в телефоне, у кассы покажите QR. Бариста сканирует — и на карте оттиск, +${RULES.perCup} баллов.</p></div>
          <div class="step" style="--d:.3s"><span class="no">3</span><b>${SLOTS()}-й оттиск — кофе бесплатно</b><p>На ${RULES.freeCost} баллах закажите маленький или большой за баллы. Карта начнётся заново.</p></div>
        </div>
      </section>
      <section class="auth" id="auth">
        <div class="rv">
          <h2>Ваша карта</h2>
          <p>Баллы хранятся у кофейни: сменили телефон — войдите тем же номером.</p>
          <ul class="perks">
            <li>${ico("check")}Оттиск появляется на экране, как только бариста отсканирует QR</li>
            <li>${ico("check")}Платите как привыкли — наличными или картой на кассе</li>
            <li>${ico("check")}В истории виден каждый стакан</li>
          </ul>
        </div>
        <div class="card rv" style="--d:.1s" id="authCard">${authForm("register")}</div>
      </section>
      <footer class="foot"><a class="stafflink" href="#staff">${ico("coffee", 18)}Вход для сотрудников</a><p>Кофейня Международного университета Кыргызской Республики</p></footer>`;
    bindAuth();
    reveal();
    demoLoop(viewId);
    tilt();
  }

  // Сцена на главной: штамп сам ставит оттиски на карту, на последнем — «Кофе бесплатно», и заново.
  async function demoLoop(id) {
    const stage = document.getElementById("stage"), tool = document.getElementById("tool");
    const card = document.getElementById("demo"), grid = document.getElementById("demoSlots"), badge = document.getElementById("badge");
    if (!stage) return;
    let visible = true;
    if ("IntersectionObserver" in window) new IntersectionObserver(e => (visible = e[0].isIntersecting)).observe(stage);
    const alive = () => id === viewId && document.body.contains(stage);
    const n = SLOTS(), paper = "#1B0E7A";
    if (!MOTION) { grid.innerHTML = slotsHtml(40, null, paper); countTo(card.querySelector(".pts"), 0, 40); return; }
    await sleep(1400);
    let pos = null;
    const place = (x, y, o = 1, ms = 520, ease = "cubic-bezier(.2,.8,.2,1)") => {
      const a = tool.animate([
        { transform: `translate(${pos ? pos.x : x}px, ${pos ? pos.y : y - 60}px)`, opacity: pos ? 1 : 0 },
        { transform: `translate(${x}px, ${y}px)`, opacity: o },
      ], { duration: ms, easing: ease, fill: "forwards" });
      pos = { x, y }; return a.finished;
    };
    while (alive()) {
      for (let i = 0; i < n && alive(); i++) {
        while (!visible && alive()) await sleep(300);
        const s = grid.children[i].getBoundingClientRect(), st = stage.getBoundingClientRect();
        const x = s.left - st.left + s.width / 2 - 38, y = s.top - st.top + s.height / 2 - 104;
        await place(x, y - 34);                                        // навели
        await place(x, y, 1, 140, "cubic-bezier(.6,0,.9,.4)");         // прижали
        if (!alive()) return;
        grid.children[i].outerHTML = `<div class="slot on fresh" style="--rot:${rot(i)}">${stamp(paper)}</div>`;
        card.classList.remove("hit"); void card.offsetWidth; card.classList.add("hit");
        countTo(card.querySelector(".pts"), i * RULES.perCup, (i + 1) * RULES.perCup, 420);
        await place(x, y - 44, 1, 260);                                // подняли
        await sleep(260);
      }
      if (!alive()) return;
      const st = stage.getBoundingClientRect();
      await place(st.width - 60, -60, 0, 600);
      pos = null;
      card.classList.add("ready"); badge.classList.add("show");
      await sleep(2600);
      if (!alive()) return;
      badge.classList.remove("show"); card.classList.remove("ready");
      await grid.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 350, fill: "forwards" }).finished;
      grid.innerHTML = slotsHtml(0, null, paper); grid.getAnimations().forEach(a => a.cancel());
      countTo(card.querySelector(".pts"), RULES.freeCost, 0, 500);
      await sleep(900);
    }
  }

  // Карта на главной поворачивается за курсором.
  function tilt() {
    const stage = document.getElementById("stage");
    if (!stage || !MOTION || !matchMedia("(pointer: fine)").matches) return;
    const t = stage.querySelector(".tilt");
    stage.onpointermove = e => {
      const r = stage.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - .5, y = (e.clientY - r.top) / r.height - .5;
      t.style.transform = `rotate(-3deg) rotateY(${x * 14}deg) rotateX(${-y * 12}deg)`;
    };
    stage.onpointerleave = () => (t.style.transform = "");
  }

  function authForm(mode) {
    const reg = mode === "register";
    return `
      <div class="tabs" role="tablist" data-on="${mode}">
        <button role="tab" aria-selected="${reg}" data-tab="register">Регистрация</button>
        <button role="tab" aria-selected="${!reg}" data-tab="login">Вход</button>
      </div>
      <div id="formHost">${formHtml(mode)}</div>`;
  }
  function formHtml(mode, anim) {
    const reg = mode === "register";
    return `<form class="form${anim ? " swap" : ""}" data-mode="${mode}" novalidate>
        ${reg ? `<div class="two">
          <div class="field"><label for="f-name">Имя и фамилия</label><input id="f-name" name="name" autocomplete="name" maxlength="60" required></div>
          <div class="field"><label for="f-group">Группа</label><input id="f-group" name="group" placeholder="ПИ-2" maxlength="20" required></div>
        </div>
        <div class="trap" aria-hidden="true"><label>Сайт <input name="website" tabindex="-1" autocomplete="off"></label></div>` : ""}
        <div class="field"><label for="f-phone">Телефон</label>
          <input id="f-phone" name="phone" type="tel" inputmode="tel" autocomplete="${reg ? "tel" : "username"}" placeholder="0700 123 456" maxlength="20" required>
          ${reg ? "" : '<div class="hint">Вы бариста или администрация? <a href="#staff">Вход для сотрудников</a></div>'}</div>
        <div class="field"><label for="f-pass">Пароль</label>
          <div class="pw"><input id="f-pass" name="password" type="password" autocomplete="${reg ? "new-password" : "current-password"}" minlength="6" maxlength="128" required>
          <button type="button" class="pw-eye" aria-label="Показать пароль" aria-pressed="false">Показать</button></div>
          <div class="hint">${reg ? "Не короче 6 символов." : "Забыли пароль? Подойдите к бариста — администрация выдаст новый."}</div></div>
        <p class="err" id="f-err" role="alert"></p>
        <button class="btn wide" type="submit">${reg ? "Завести карту" : "Войти"}</button>
      </form>`;
  }

  function setMode(mode, focus) {
    const host = document.getElementById("authCard"), tabs = host.querySelector(".tabs");
    if (tabs.dataset.on !== mode) {
      tabs.dataset.on = mode;
      tabs.querySelectorAll("button").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === mode));
      document.getElementById("formHost").innerHTML = formHtml(mode, true);
      bindForm();
    }
    if (focus) setTimeout(() => host.querySelector("input").focus({ preventScroll: true }), 450);
  }

  function bindAuth() {
    $app.querySelectorAll("[data-go]").forEach(b => b.onclick = () => {
      setMode(b.dataset.go, true);
      document.getElementById("auth").scrollIntoView({ behavior: MOTION ? "smooth" : "auto" });
    });
    $app.querySelectorAll("[data-tab]").forEach(b => b.onclick = () => setMode(b.dataset.tab));
    bindForm();
  }

  function bindEye(f) {
    const eye = f.querySelector(".pw-eye");
    if (eye) eye.onclick = () => {
      const i = f.querySelector(".pw input"), show = i.type === "password";
      i.type = show ? "text" : "password"; eye.textContent = show ? "Скрыть" : "Показать"; eye.setAttribute("aria-pressed", show);
    };
  }

  function bindForm() {
    const f = document.querySelector("#formHost form");
    bindEye(f);
    f.onsubmit = async e => {
      e.preventDefault();
      const btn = f.querySelector("button[type=submit]"), err = f.querySelector(".err");
      const body = Object.fromEntries(new FormData(f));
      btn.disabled = true; err.textContent = ""; f.classList.remove("bad");
      try {
        const r = await api(f.dataset.mode, body);
        store.tok = r.token; state.user = r.user;
        toast(f.dataset.mode === "register" ? "Карта готова — нажмите на неё, чтобы увидеть QR" : "С возвращением, " + r.user.name.split(" ")[0]);
        window.scrollTo(0, 0); render();
      } catch (x) {
        err.textContent = x.message; btn.disabled = false;
        void f.offsetWidth; f.classList.add("bad");
      }
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

  // Заказ, который ещё ждёт бариста: не оплачен на кассе или оплачен, но не выдан.
  const waitingOrder = me => {
    const o = me?.order;
    return o && (o.status === "paid" || (o.status === "pending" && DESK(o.provider))) ? o : null;
  };

  function viewStudent(firstDraw) {
    const u = state.user, me = state.me;
    const points = me ? me.user.points : u.points;
    const prev = state.lastPts;
    const freshFrom = prev !== null && points > prev ? Math.floor(prev / RULES.perCup) : null;
    const becameReady = prev !== null && prev < RULES.freeCost && points >= RULES.freeCost;
    state.lastPts = points;
    const hist = me ? me.history : [];
    const seen = state.lastOp;
    state.lastOp = hist.length ? hist[0].id : seen;
    const wo = waitingOrder(me);
    $app.innerHTML = `
      ${headerUser(esc(u.group) || "Бонусная карта")}
      <div class="shell${firstDraw ? "" : " calm"}">
        <div class="hello"><h1>Привет, ${first(u.name)}</h1></div>
        <section class="pass-wrap" aria-label="Ваша карта">
          ${passHtml(u, points, { prev: prev ?? points, freshFrom })}
          <p class="pass-cap" id="passCap">${leftText(points)}</p>
        </section>
        ${wo ? `<button class="waiting" id="wo" type="button">
            <span class="w-ic">${ico("qr", 24)}</span>
            <span><b>${wo.num ? `Заказ №${wo.num}` : size(wo.size) + " кофе"} ждёт у кассы</b>
            <small>${wo.free ? "За баллы" : DESK(wo.provider) && wo.status === "pending" ? `${wo.amount} сом — оплата на кассе` : "Оплачено"}. Покажите QR бариста.</small></span>
          </button>` : orderBlock(points)}
        ${me ? `<div class="facts">
          <div class="fact"><b>${me.cups}</b><span>${plural(me.cups, "стакан", "стакана", "стаканов")} по карте</span></div>
          <div class="fact"><b>${me.free}</b><span>${plural(me.free, "бесплатный кофе", "бесплатных кофе", "бесплатных кофе")}</span></div>
        </div>` : ""}
        <section class="hist">
          <h2>История</h2>
          ${hist.length ? `<ul class="list">${(state.histAll ? hist : hist.slice(0, 6)).map(o => `<li class="${seen && o.id > seen ? "new" : ""}">
            <span class="dot${o.kind === "redeem" ? " free" : ""}">${ico(o.kind === "redeem" ? "gift" : "coffee", 18)}</span>
            <span class="what"><b>${o.kind === "redeem" ? "Бесплатный кофе" : "Кофе"} · ${size(o.size).toLowerCase()}</b><span>${fmtTime(o.ts)}</span></span>
            <span class="d ${o.delta > 0 ? "plus" : "minus"}">${o.delta > 0 ? "+" : "−"}${Math.abs(o.delta)}</span></li>`).join("")}${!state.histAll && hist.length > 6 ? `<li class="more-row"><button class="more" id="more">Показать всю историю</button></li>` : ""}</ul>`
          : `<div class="empty">Здесь появится каждый стакан. Первый оттиск — на кассе.</div>`}
        </section>
      </div>
      <div class="dock"><button class="btn wide" id="showQr">${ico("qr")}Показать QR бариста</button></div>`;
    document.getElementById("logout").onclick = logout;
    const pass = document.getElementById("pass");
    pass.onclick = () => { const on = pass.classList.toggle("flipped"); pass.setAttribute("aria-pressed", on); };
    document.getElementById("showQr").onclick = () => fullQr(u.code);
    const more = document.getElementById("more");
    if (more) more.onclick = () => { state.histAll = true; viewStudent(false); };
    bindOrder();
    const w = document.getElementById("wo");
    if (w) w.onclick = () => orderSheet(me.order);
    // Вернулись в приложение — снова показываем заказ, который ждёт бариста.
    if (wo && !sheet && firstDraw) orderSheet(me.order);
    countTo(pass.querySelector(".pts"), prev, points);
    if (freshFrom !== null) {
      navigator.vibrate?.([30, 50, 30]);
      toast(`+${pts(points - prev)} — спасибо за кофе!`);
      pass.classList.add("stamped"); setTimeout(() => pass.classList.remove("stamped"), 900);
    }
    if (becameReady) setTimeout(() => { beans(pass); toast(`${RULES.freeCost} баллов — следующий кофе бесплатно!`); }, 600);
  }

  // Выбор кофе: размер плиткой, одна кнопка «Заказать»; бесплатный — отдельным блоком, когда накопилось.
  function orderBlock(points) {
    const p = RULES.prices, ready = points >= RULES.freeCost, sz = ls.get("mc.size") || "s";
    if (RULES.pay === "off" && !ready) return "";
    const how = RULES.pay === "gopay" ? "Оплата в приложении банка, потом покажите QR на кассе."
      : "Оплатите на кассе наличными или картой. Баллы придут, когда бариста отсканирует QR.";
    return `<section class="card order" aria-label="Заказать кофе">
      ${ready ? `<div class="gift-row">
          <div class="g-head">${ico("gift")}<b>Бесплатный кофе готов</b></div>
          <div class="sizes gift">
            <button class="size" data-free="s"><b>Маленький</b><span>за ${RULES.freeCost} баллов</span></button>
            <button class="size" data-free="l"><b>Большой</b><span>за ${RULES.freeCost} баллов</span></button>
          </div></div>` : ""}
      ${RULES.pay !== "off" ? `<div class="card-head"><span>Заказать кофе</span>${RULES.pay === "demo" ? `<span class="tag">Демо</span>` : ""}</div>
        <div class="sizes pick" role="radiogroup" aria-label="Размер">
          <button class="size" role="radio" aria-checked="${sz === "s"}" data-size="s"><b>Маленький</b><span>${p.s} сом</span></button>
          <button class="size" role="radio" aria-checked="${sz === "l"}" data-size="l"><b>Большой</b><span>${p.l} сом</span></button>
        </div>
        <button class="btn wide go" id="orderGo">Заказать за ${p[sz]} сом</button>
        <p class="sub pay-how">${how}</p>` : ""}
    </section>`;
  }
  function bindOrder() {
    $app.querySelectorAll("[data-size]").forEach(b => b.onclick = () => {
      ls.set("mc.size", b.dataset.size);
      $app.querySelectorAll("[data-size]").forEach(x => x.setAttribute("aria-checked", x === b));
      document.getElementById("orderGo").textContent = `Заказать за ${RULES.prices[b.dataset.size]} сом`;
    });
    const go = document.getElementById("orderGo");
    if (go) go.onclick = () => orderCoffee(ls.get("mc.size") || "s", false, go);
    $app.querySelectorAll("[data-free]").forEach(b => b.onclick = () => orderCoffee(b.dataset.free, true, b));
  }

  function fullQr(code) {
    const d = document.createElement("div");
    d.className = "full"; d.setAttribute("role", "dialog"); d.setAttribute("aria-label", "QR-код");
    d.innerHTML = `<button class="iconbtn" aria-label="Закрыть">${ico("x", 26)}</button>
      <div class="box">${qrSvg(code, "q")}<div class="code">${fmtCode(code)}</div>
      <p class="bright">${ico("sun", 16)}Прибавьте яркость — камера считает быстрее</p></div>`;
    const close = () => { d.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = e => e.key === "Escape" && close();
    d.onclick = close; document.addEventListener("keydown", onKey);
    document.body.appendChild(d); d.querySelector("button").focus();
  }

  // ---------- заказ ученика: шторка с QR и ходом заказа ----------
  const BANKS = { mbank: "MBank", megapay: "MegaPay", optima: "Optima24", odengi: "О!Деньги", bakai: "Bakai24",
    elcart: "Элкарт", keremet: "Keremet", demir: "Demir", rsk: "РСК", kicb: "KICB", aiyl: "Айыл Банк", balance: "Balance" };
  const bankName = k => BANKS[k.toLowerCase()] || k.charAt(0).toUpperCase() + k.slice(1);
  const qrOf = data => { const q = qrcode(0, "M"); q.addData(data); q.make(); return q.createSvgTag({ cellSize: 4, margin: 0, scalable: true }); };
  let sheet = null; // открытая шторка: { el, order, timer, opts }

  function openSheet(label) {
    closeSheet();
    const el = document.createElement("div");
    el.className = "sheet-wrap"; el.setAttribute("role", "dialog"); el.setAttribute("aria-modal", "true"); el.setAttribute("aria-label", label);
    el.innerHTML = `<div class="sheet"></div>`;
    document.body.appendChild(el);
    return el;
  }
  function orderSheet(order, opts = {}) {
    const el = openSheet("Заказ");
    el.addEventListener("click", e => { if (e.target === el) closeSheet(); });
    sheet = { el, order, opts, timer: 0, tick: 0, onClose: () => refreshMe(true) };
    drawSheet();
    if (live(order)) pollOrder();
  }
  // Ученик ждёт бариста: опрашиваем и неоплаченный, и оплаченный, но не выданный заказ.
  const live = o => o.status === "pending" || o.status === "paid";
  function closeSheet() {
    if (!sheet) return;
    clearTimeout(sheet.timer); clearInterval(sheet.tick);
    const el = sheet.el, after = sheet.onClose; sheet = null;
    el.classList.add("out"); setTimeout(() => el.remove(), MOTION ? 280 : 0);
    after?.();
  }
  async function pollOrder() {
    if (!sheet || !live(sheet.order)) return;
    const s = sheet;
    try {
      const r = await api("orders/" + s.order.id);
      if (s !== sheet) return;
      const was = s.order.status;
      s.order = Object.assign(s.order, r.order);
      if (r.order.status !== was) {
        drawSheet();
        if (r.order.status === "served") {
          // Бариста отсканировал и выдал: шторка уходит, оттиск ставится на карту у ученика на глазах.
          navigator.vibrate?.([30, 60, 30]);
          s.onClose = () => refreshMe();
          s.timer = setTimeout(() => sheet === s && closeSheet(), 1500);
          return;
        }
        if (!live(s.order)) return;
      }
    } catch {}
    if (sheet === s) s.timer = setTimeout(pollOrder, document.visibilityState === "visible" ? 2000 : 6000);
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && sheet && !sheet.guest && live(sheet.order)) { clearTimeout(sheet.timer); pollOrder(); }
  });

  // Ход заказа: три шага, текущий подсвечен.
  const stepsHtml = (at, free) => `<ol class="track" aria-label="Ход заказа">
      ${["Заказ принят", "Бариста сканирует QR", free ? "Кофе ваш" : `+${RULES.perCup} баллов`].map((t, i) =>
        `<li class="${i < at ? "done" : i === at ? "now" : ""}"><i>${i < at ? ico("check", 14) : ""}</i>${t}</li>`).join("")}
    </ol>`;

  function drawSheet() {
    const { order: o } = sheet, box = sheet.el.querySelector(".sheet");
    clearInterval(sheet.tick);
    const what = `${o.free ? "Бесплатный " + size(o.size).toLowerCase() : size(o.size)} кофе`;
    const close = `<button class="iconbtn sheet-x" data-close aria-label="Закрыть">${ico("x")}</button>`;
    const myQr = `<div class="payqr big">${qrSvg(state.user.code)}</div><div class="p-code center">${fmtCode(state.user.code)}</div>`;
    if (o.status === "pending" && DESK(o.provider)) {
      box.innerHTML = `${close}<div class="sh-head"><b>${what}</b><span>${o.amount} сом · оплата на кассе</span></div>
        ${stepsHtml(1)}
        ${myQr}
        <p class="cap"><b>Покажите QR бариста</b> и оплатите наличными или картой. Заказ ждёт ещё <span id="left"></span>.</p>
        <button class="linkbtn" data-cancel>Отменить заказ</button>`;
    } else if (o.status === "pending") {
      const links = Object.entries(o.appLinks || {}).filter(([, u]) => u);
      box.innerHTML = `${close}<div class="sh-head"><b>${what}</b><span>${o.amount} сом</span></div>
        <div class="wait"><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span><span>Ждём оплату</span><span class="left" id="left"></span></div>
        ${links.length ? `<p class="sh-label">Оплатите в приложении своего банка</p>
          <div class="banks">${links.map(([k, u]) => `<a class="bank" href="${esc(u)}">${ico("out", 18)}${esc(bankName(k))}</a>`).join("")}</div>` : ""}
        ${o.checkoutUrl ? `<a class="btn soft wide" href="${esc(o.checkoutUrl)}" target="_blank" rel="noopener">Все банки и Элкарт</a>` : ""}
        ${o.qrData ? `<div class="payqr">${qrOf(o.qrData)}</div><p class="cap">Платите с другого телефона? Отсканируйте этот код в приложении банка.</p>` : ""}
        <button class="linkbtn" data-cancel>Отменить заказ</button>`;
    } else if (o.status === "paid") {
      box.innerHTML = `${close}<div class="sh-head"><b>${what}</b><span>${o.free ? "за баллы" : "оплачено"}${o.num ? " · заказ №" + o.num : ""}</span></div>
        ${stepsHtml(1, o.free)}
        ${myQr}
        <p class="cap"><b>Покажите QR бариста</b> — он отсканирует и отдаст кофе.</p>`;
    } else if (o.status === "served") {
      box.innerHTML = `<div class="done">
          <div class="big-stamp">${stamp()}</div>
          <b>${o.free ? "Приятного кофе!" : `+${RULES.perCup} баллов`}</b>
          <p>${o.free ? "Бесплатный кофе выдан, карта начинается заново." : "Оттиск уже на карте. Приятного кофе!"}</p>
          ${o.test ? `<span class="demo">Демо</span>` : ""}
        </div>`;
    } else {
      const why = { expired: "Время заказа вышло", cancelled: "Заказ отменён", failed: "Банк не провёл оплату" }[o.status] || "Оплата не прошла";
      box.innerHTML = `${close}<div class="done fail"><b>${why}</b><p>Деньги не списаны. Можно оформить заказ ещё раз.</p></div>
        <button class="btn wide" data-close>Понятно</button>`;
    }
    box.querySelectorAll("[data-close]").forEach(b => b.onclick = closeSheet);
    const left = box.querySelector("#left");
    if (left) {
      const upd = () => { const s = Math.max(0, o.expires - Date.now() / 1000); left.textContent = `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`; };
      upd(); sheet.tick = setInterval(upd, 1000);
    }
    const c = box.querySelector("[data-cancel]");
    if (c) c.onclick = async () => {
      c.disabled = true;
      try { const r = await api(`orders/${o.id}/cancel`, {}); sheet.order.status = r.order.status; sheet.onClose = () => refreshMe(true); drawSheet(); }
      catch (x) { toast(x.message, true); c.disabled = false; }
    };
  }
  document.addEventListener("keydown", e => { if (e.key === "Escape" && sheet) closeSheet(); });

  // Ученик оформляет заказ: обычный (оплата на кассе или в банке) или бесплатный за баллы.
  async function orderCoffee(sz, free, btn) {
    btn && (btn.disabled = true);
    try {
      const r = await api("orders", { size: sz, free });
      orderSheet(r.order);
      sheet.onClose = () => refreshMe(true);
      refreshMe(true);
    } catch (x) { toast(x.message, true); }
    btn && (btn.disabled = false);
  }

  async function refreshMe(force) {
    try {
      const me = await api("me");
      const key = m => JSON.stringify([m.user.points, m.history[0]?.id, m.order && [m.order.id, m.order.status]]);
      const changed = force || !state.me || key(me) !== key(state.me);
      state.me = me; state.user = me.user;
      if (changed && state.user.role === "student" && !sheet) viewStudent(false);
    } catch {}
  }

  // ---------- касса ----------
  let cam = { stream: null, raf: 0, busy: false };

  function viewDesk() {
    $app.innerHTML = headerUser("Касса · " + esc(state.user.name)) + deskHtml();
    document.getElementById("logout").onclick = logout;
    bindDesk();
  }

  // Касса: итоги дня полосой, сканер и очередь рядом, операции дня ниже. Встраивается и в кабинет администрации.
  function deskHtml() {
    return `
      <div class="desk">
        <section class="today" id="today" aria-label="Сегодня"></section>
        <div class="desk-main">
          <section class="scan" aria-label="Сканер">
            <div class="viewport" id="vp">
              <div class="idle">${ico("camera", 32)}<b>Сканер QR</b><span>Наведите камеру на QR в телефоне ученика</span>
                <button class="btn foam" id="camOn">Включить камеру</button></div>
            </div>
            <form class="manual" id="manual">
              <label class="sr" for="m-code">Код карты</label>
              <input id="m-code" placeholder="Код карты: XXXX-XXXX" autocomplete="off" autocapitalize="characters" spellcheck="false" maxlength="40">
              <button class="btn foam" type="submit">${ico("search", 18)}<span class="sr">Найти</span></button>
            </form>
          </section>
          <section class="queue" id="queue" aria-live="polite" aria-label="Заказы, которые ждут"></section>
        </div>
        <details class="ops" id="ops"><summary>Операции за сегодня ${ico("down", 18)}</summary><div id="opsList"></div></details>
      </div>`;
  }
  function bindDesk() {
    document.getElementById("camOn").onclick = startCam;
    const m = document.getElementById("m-code");
    // Ручной ввод и USB-сканер (он «печатает» содержимое QR и Enter) — оба сюда.
    document.getElementById("manual").onsubmit = e => {
      e.preventDefault();
      const raw = m.value.trim();
      const v = raw.startsWith(QR_PREFIX) || raw.startsWith(PICKUP) ? raw : raw.replace(/[^a-z0-9]/gi, "");
      if (!raw.startsWith(PICKUP) && v.replace(QR_PREFIX, "").length < 8) return toast("В коде карты 8 знаков", true);
      m.value = ""; onScan(v.startsWith(QR_PREFIX) || v.startsWith(PICKUP) ? v : QR_PREFIX + v);
    };
    if (ls.get("mc.cam") === "1") startCam();
    loadToday(); loadQueue();
  }

  // ---------- очередь заказов ----------
  let queueSeen = null, audio = null;
  // Мягкий двухнотный сигнал о новом заказе (звук включается после первого нажатия на странице).
  function chime() {
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      const t = audio.currentTime;
      [[784, 0], [1047, .16]].forEach(([f, d]) => {
        const o = audio.createOscillator(), g = audio.createGain();
        o.type = "sine"; o.frequency.value = f;
        g.gain.setValueAtTime(0, t + d); g.gain.linearRampToValueAtTime(.18, t + d + .02); g.gain.exponentialRampToValueAtTime(.001, t + d + .5);
        o.connect(g).connect(audio.destination); o.start(t + d); o.stop(t + d + .55);
      });
    } catch {}
  }
  document.addEventListener("pointerdown", () => audio?.state === "suspended" && audio.resume(), { passive: true });

  const orderLine = o => o.free ? "за баллы" : o.status === "pending" ? `${o.amount} сом · оплата на кассе` : `${o.amount} сом · оплачено`;

  async function loadQueue() {
    const el = document.getElementById("queue");
    if (!el) return;
    let q;
    try { q = await api("queue"); } catch { return; }
    const ids = q.waiting.map(o => o.id);
    const isNew = queueSeen ? ids.filter(i => !queueSeen.includes(i)) : [];
    queueSeen = ids;
    if (isNew.length) { chime(); navigator.vibrate?.([60, 40, 60]); }
    el.innerHTML = `<div class="q-head"><h2>Ждут кофе${q.waiting.length ? ` <span class="q-count">${q.waiting.length}</span>` : ""}</h2>
        <span class="q-live"><i></i>${q.pending ? `ждут оплату в банке: ${q.pending}` : "обновляется само"}</span></div>
      ${q.waiting.length ? `<div class="q-list">${q.waiting.map(o => `
        <article class="q-card${isNew.includes(o.id) ? " new" : ""}${o.free ? " free" : ""}">
          <div class="q-num">${o.num ? "№" + o.num : esc(size(o.size).slice(0, 1))}</div>
          <div class="q-who"><b>${esc(o.name)}</b><span>${esc(o.group)} · ${size(o.size).toLowerCase()}</span>
            <em>${orderLine(o)}${o.test ? " · демо" : ""} · ${ago(o.paidAt || o.created)}</em></div>
        </article>`).join("")}</div>
        <p class="q-note">${ico("qr", 16)}Выдать можно только после сканирования QR ученика.</p>`
      : `<div class="q-empty">${ico("coffee", 28)}<p>Заказов пока нет. Новые появятся здесь сами — со звуком.</p></div>`}`;
  }

  async function startCam() {
    const vp = document.getElementById("vp");
    if (!navigator.mediaDevices?.getUserMedia) return toast("Браузер не даёт камеру — введите код вручную", true);
    try {
      cam.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
    } catch { ls.set("mc.cam", ""); return toast("Нет доступа к камере. Разрешите её в настройках браузера", true); }
    if (!document.body.contains(vp)) return stopCam();
    ls.set("mc.cam", "1");
    vp.innerHTML = `<video playsinline muted></video><div class="frame"></div><button class="iconbtn cam-off" id="camOff" aria-label="Выключить камеру">${ico("x")}</button>`;
    document.getElementById("camOff").onclick = () => { ls.set("mc.cam", ""); stopCam(); viewIdRedrawScan(); };
    const v = vp.querySelector("video"); v.srcObject = cam.stream; await v.play().catch(() => {});
    const c = document.createElement("canvas"), ctx = c.getContext("2d", { willReadFrequently: true });
    let last = 0;
    const tick = now => {
      cam.raf = requestAnimationFrame(tick);
      if (cam.busy || sheet || now - last < 120 || v.readyState < 2 || !window.jsQR) return;  // ~8 кадров в секунду хватает
      last = now;
      const w = 480, h = Math.round(v.videoHeight / v.videoWidth * w) || 360;
      c.width = w; c.height = h; ctx.drawImage(v, 0, 0, w, h);
      const r = jsQR(ctx.getImageData(0, 0, w, h).data, w, h, { inversionAttempts: "dontInvert" });
      if (r && (r.data.startsWith(QR_PREFIX) || r.data.startsWith(PICKUP))) {
        cam.busy = true; navigator.vibrate?.(60);
        vp.classList.remove("hit"); void vp.offsetWidth; vp.classList.add("hit");
        onScan(r.data).finally(() => setTimeout(() => (cam.busy = false), 1200));
      }
    };
    cam.raf = requestAnimationFrame(tick);
  }
  function viewIdRedrawScan() {
    const vp = document.getElementById("vp");
    if (!vp) return;
    vp.innerHTML = `<div class="idle">${ico("camera", 32)}<b>Сканер QR</b><span>Наведите камеру на QR в телефоне ученика</span>
      <button class="btn foam" id="camOn">Включить камеру</button></div>`;
    document.getElementById("camOn").onclick = startCam;
  }
  function stopCam() {
    cancelAnimationFrame(cam.raf);
    cam.stream?.getTracks().forEach(t => t.stop()); cam.stream = null;
  }

  // Отсканировали QR ученика: открываем его панель — заказы к выдаче и продажа на кассе.
  async function onScan(qr) {
    try {
      const r = await api("orders/scan", { qr });
      state.guest = r.card; state.guestOrders = r.orders;
      guestSheet();
    } catch (x) { toast(x.message, true); }
  }

  function guestSheet() {
    const el = openSheet("Ученик");
    el.classList.add("guest-wrap");
    el.addEventListener("click", e => { if (e.target === el) closeSheet(); });
    sheet = { el, guest: true, timer: 0, tick: 0 };
    sheet.onClose = () => { state.guest = null; state.guestOrders = []; };
    drawGuest();
  }

  function drawGuest(after) {
    if (!sheet?.guest) return;
    const g = state.guest, box = sheet.el.querySelector(".sheet"), p = RULES.prices;
    const can = g.points >= RULES.freeCost, orders = state.guestOrders || [];
    const sz = sheet.sz || "s";
    const freshFrom = after?.delta > 0 ? Math.floor((g.points - after.delta) / RULES.perCup) : null;
    box.innerHTML = `
      <button class="iconbtn sheet-x" data-close aria-label="Закрыть">${ico("x")}</button>
      <div class="who"><b>${esc(g.name)}</b><span>${esc(g.group)} · ${fmtCode(g.code)}</span></div>
      <div class="g-card${can ? " ready" : ""}">
        ${ptsHtml(after ? after.before : g.points)}
        <div class="stamps row">${slotsHtml(g.points, freshFrom)}</div>
        <p class="card-foot">${leftText(g.points, true)}</p>
      </div>
      ${after ? `<div class="g-done">${ico("check", 22)}<b>${after.text}</b></div>` : ""}
      ${orders.length ? `<div class="g-sec"><h3>Ждёт выдачи</h3>${orders.map(o => `<div class="serve">
          <div class="s-what"><b>${o.num ? "№" + o.num + " · " : ""}${size(o.size)}${o.free ? " · бесплатный" : ""}</b><span>${orderLine(o)}${o.test ? " · демо" : ""}</span></div>
          ${o.status === "pending" && DESK(o.provider)
            ? `<div class="pay2"><button class="btn pay" data-serve="${o.id}" data-pay="cash">${ico("cash")}Наличными · выдать</button>
                 <button class="btn pay" data-serve="${o.id}" data-pay="card">${ico("card")}Картой · выдать</button></div>`
            : `<button class="btn pay" data-serve="${o.id}">${ico("check")}Выдать${o.free ? "" : ` · +${RULES.perCup}`}</button>`}
        </div>`).join("")}</div>` : ""}
      <div class="g-sec">
        <h3>${orders.length ? "Ещё продажа" : "Продажа на кассе"}</h3>
        <div class="seg" role="radiogroup" aria-label="Размер">
          <button role="radio" aria-checked="${sz === "s"}" data-sz="s">Маленький · ${p.s}</button>
          <button role="radio" aria-checked="${sz === "l"}" data-sz="l">Большой · ${p.l}</button></div>
        <div class="pay2">
          <button class="act" data-sell="cash">${ico("cash")}<b>Наличными</b><em>+${RULES.perCup}</em></button>
          <button class="act" data-sell="card">${ico("card")}<b>Картой</b><em>+${RULES.perCup}</em></button>
        </div>
        ${RULES.pay === "gopay" ? `<button class="linkbtn" data-qr>Оплата по QR на экране кассы</button>` : ""}
      </div>
      ${can ? `<div class="g-sec"><h3>За баллы</h3><div class="pay2">
          <button class="act redeem" data-redeem="s">${ico("gift")}<b>Маленький</b><em>−${RULES.freeCost}</em></button>
          <button class="act redeem" data-redeem="l">${ico("gift")}<b>Большой</b><em>−${RULES.freeCost}</em></button></div></div>` : ""}`;
    countTo(box.querySelector(".pts"), after ? after.before : null, g.points, 500);
    box.querySelectorAll("[data-close]").forEach(b => b.onclick = closeSheet);
    box.querySelectorAll("[data-sz]").forEach(b => b.onclick = () => { sheet.sz = b.dataset.sz; drawGuest(); });
    const lock = () => box.querySelectorAll("button.btn, button.act").forEach(x => (x.disabled = true));
    const done = (r, text, before) => {
      state.guest = r.card; if (r.orders) state.guestOrders = r.orders;
      drawGuest({ delta: r.delta ?? r.op?.delta ?? 0, before, text });
      if (before < RULES.freeCost && r.card.points >= RULES.freeCost) beans(box);
      loadToday(); loadQueue();
      // Больше выдавать нечего — панель закрывается сама, касса снова ждёт QR.
      clearTimeout(sheet.timer);
      if (!state.guestOrders.length) { const s = sheet; s.timer = setTimeout(() => sheet === s && closeSheet(), 2200); }
    };
    box.querySelectorAll("[data-serve]").forEach(b => b.onclick = async e => {
      ripple(b, e); lock();
      const before = g.points;
      try {
        const r = await api(`orders/${b.dataset.serve}/serve`, { code: g.code, pay: b.dataset.pay });
        done(r, r.delta ? `Выдано · +${r.delta} ${ptsWord(r.delta)}` : "Выдано", before);
      } catch (x) { toast(x.message, true); drawGuest(); }
    });
    box.querySelectorAll("[data-sell]").forEach(b => b.onclick = async e => {
      ripple(b, e); lock();
      const before = g.points;
      try {
        const r = await api(`card/${g.code}/add`, { size: sz, pay: b.dataset.sell });
        done(r, `${size(sz)}, ${b.dataset.sell === "card" ? "картой" : "наличными"} · +${r.op.delta}`, before);
      } catch (x) { toast(x.message, true); drawGuest(); }
    });
    box.querySelectorAll("[data-redeem]").forEach(b => b.onclick = async e => {
      ripple(b, e); lock();
      const before = g.points;
      try {
        const r = await api(`card/${g.code}/redeem`, { size: b.dataset.redeem });
        done(r, `Бесплатный ${size(b.dataset.redeem).toLowerCase()} выдан`, before);
      } catch (x) { toast(x.message, true); drawGuest(); }
    });
    const qrb = box.querySelector("[data-qr]");
    if (qrb) qrb.onclick = async () => {
      try { const r = await api("orders", { size: sz, code: g.code }); closeSheet(); deskQrSheet(r.order, g); }
      catch (x) { toast(x.message, true); }
    };
  }

  // Касса показывает банковский QR на своём экране (только когда подключён GoPay).
  function deskQrSheet(order, g) {
    const el = openSheet("Оплата по QR");
    sheet = { el, order, opts: { desk: true }, timer: 0, tick: 0 };
    const box = el.querySelector(".sheet");
    box.innerHTML = `<button class="iconbtn sheet-x" data-close aria-label="Закрыть">${ico("x")}</button>
      <div class="sh-head"><b>${size(order.size)} · ${order.amount} сом</b><span>${esc(g.name)}</span></div>
      <div class="payqr big">${qrOf(order.qrData || "")}</div>
      <p class="cap">Ученик сканирует код в приложении банка. Когда оплата придёт, заказ появится в очереди — выдайте его по QR ученика.</p>`;
    box.querySelector("[data-close]").onclick = closeSheet;
  }

  async function loadToday() {
    const el = document.getElementById("today");
    if (!el) return;
    const seen = state.today?.ops[0]?.id || 0;
    try { state.today = await api("today"); } catch { return; }
    const t = state.today;
    el.innerHTML = `
      <div class="t-fact"><b>${t.sold}</b><span>${plural(t.sold, "стакан", "стакана", "стаканов")}</span></div>
      <div class="t-fact"><b>${fmtSom(t.revenue)}</b><span>сом сегодня</span></div>
      <div class="t-fact"><b>${fmtSom(t.cash)}</b><span>наличными</span></div>
      <div class="t-fact"><b>${fmtSom((t.card || 0) + (t.qr || 0))}</b><span>картой и QR</span></div>`;
    const ops = document.getElementById("opsList");
    if (!ops) return;
    ops.innerHTML = t.ops.length ? `<ul class="list">${t.ops.map(o => `<li class="${o.undone ? "undone" : ""}${seen && o.id > seen ? " new" : ""}">
        <span class="dot${o.kind === "redeem" ? " free" : ""}">${ico(o.kind === "redeem" ? "gift" : o.pay === "card" ? "card" : o.pay === "cash" ? "cash" : "coffee", 18)}</span>
        <span class="what"><b>${esc(o.name)}</b><span>${size(o.size).toLowerCase()} · ${o.kind === "redeem" ? "за баллы" : o.pay === "card" ? "картой" : o.pay === "qr" ? "QR" : "наличными"} · ${fmtTime(o.ts).replace("сегодня, ", "")}${o.undone ? ` · отменил${o.undoneBy ? "(а) " + esc(o.undoneBy) : "и"}` : ""}</span></span>
        ${o.canUndo ? `<button class="undo" data-op="${o.id}" aria-label="Отменить операцию">${ico("undo", 16)}<span class="t">Отменить</span></button>` : ""}
        <span class="d ${o.delta > 0 ? "plus" : "minus"}">${o.delta > 0 ? "+" : "−"}${Math.abs(o.delta)}</span></li>`).join("")}</ul>`
      : `<div class="empty">Пока ни одного стакана. Отсканируйте первый QR.</div>`;
    ops.querySelectorAll("[data-op]").forEach(b => b.onclick = async () => {
      b.disabled = true;
      try { await api(`op/${b.dataset.op}/undo`, {}); toast("Операция отменена"); loadToday(); }
      catch (x) { toast(x.message, true); b.disabled = false; }
    });
  }

  // ---------- вход для сотрудников (#staff) ----------
  function viewStaffLogin() {
    $app.innerHTML = `
      <header class="top"><a class="brand" href="#">${brandMark()}<span><b>МУКР Кофе</b><small>Для сотрудников</small></span></a>
        <a class="ghost" href="#">Я ученик</a></header>
      <section class="staff-in">
        <div class="card">
          <div class="staff-ic">${ico("coffee", 26)}</div>
          <h1>Вход для сотрудников</h1>
          <p class="sub">Касса бариста и кабинет администрации. Логин и пароль выдаёт администрация.</p>
          <form class="form" id="sform" novalidate>
            <div class="field"><label for="s-login">Логин</label><input id="s-login" name="phone" autocomplete="username" autocapitalize="none" spellcheck="false" maxlength="40" required></div>
            <div class="field"><label for="s-pass">Пароль</label><div class="pw"><input id="s-pass" name="password" type="password" autocomplete="current-password" maxlength="128" required>
              <button type="button" class="pw-eye" aria-label="Показать пароль" aria-pressed="false">Показать</button></div></div>
            <p class="err" id="s-err" role="alert"></p>
            <button class="btn wide" type="submit">Войти</button>
          </form>
        </div>
      </section>`;
    const f = document.getElementById("sform");
    bindEye(f);
    setTimeout(() => f.querySelector("input").focus(), 300);
    f.onsubmit = async e => {
      e.preventDefault();
      const btn = f.querySelector("button[type=submit]"), err = f.querySelector(".err");
      btn.disabled = true; err.textContent = ""; f.classList.remove("bad");
      try {
        const r = await api("login", Object.fromEntries(new FormData(f)));
        store.tok = r.token; state.user = r.user;
        history.replaceState(null, "", location.pathname);
        toast("Здравствуйте, " + r.user.name.split(" ")[0]); render();
      } catch (x) { err.textContent = x.message; btn.disabled = false; void f.offsetWidth; f.classList.add("bad"); }
    };
  }

  // ---------- кабинет администрации ----------
  const TABS = [["money", "Выручка"], ["desk", "Касса"], ["students", "Ученики"], ["orders", "Заказы"], ["staff", "Сотрудники"], ["settings", "Настройки"]];
  const STATUS = { pending: ["Ждёт", "wait"], paid: ["Оплачен", "ok"], served: ["Выдан", "done"],
    failed: ["Ошибка банка", "bad"], expired: ["Не забрали", "off"], cancelled: ["Отменён", "off"] };

  function viewAdmin() {
    const tab = state.tab || "money";
    $app.innerHTML = `${headerUser("Администрация · " + esc(state.user.name))}
      <nav class="tabsbar" role="tablist" aria-label="Разделы">${TABS.map(([k, t]) =>
        `<button role="tab" aria-selected="${k === tab}" data-adm="${k}">${t}</button>`).join("")}</nav>
      <div id="adm" class="adm"></div>`;
    document.getElementById("logout").onclick = logout;
    $app.querySelectorAll("[data-adm]").forEach(b => b.onclick = () => { state.tab = b.dataset.adm; stopCam(); closeSheet(); viewAdmin(); });
    const host = document.getElementById("adm");
    clearInterval(pollTimer);
    if (tab === "desk") {
      host.innerHTML = deskHtml(); bindDesk();
      pollTimer = setInterval(() => document.visibilityState === "visible" && (loadQueue(), loadToday()), 4000);
    } else if (tab === "money") admMoney(host);
    else if (tab === "students") admStudents(host);
    else if (tab === "orders") admOrders(host);
    else if (tab === "staff") admStaff(host);
    else admSettings(host);
  }

  async function admMoney(host, days = state.days || 7, test = state.withTest || false) {
    state.days = days; state.withTest = test;
    host.innerHTML = `<div class="loading small">${ico("coffee", 32)}</div>`;
    let s;
    try { s = await api(`admin/stats?days=${days}&test=${test ? 1 : 0}`); } catch (x) { host.innerHTML = `<div class="empty">${esc(x.message)}</div>`; return; }
    const card = (s.card || 0) + (s.qr || 0), share = s.revenue ? Math.round(card / s.revenue * 100) : 0;
    host.innerHTML = `
      <div class="filters">
        <div class="chips" role="radiogroup" aria-label="Период">${[[1, "Сегодня"], [7, "7 дней"], [30, "30 дней"]].map(([d, t]) =>
          `<button role="radio" aria-checked="${d === days}" data-days="${d}">${t}</button>`).join("")}</div>
        ${s.demoOps || test ? `<label class="chk"><input type="checkbox" id="wtest" ${test ? "checked" : ""}> Учитывать демо-продажи</label>` : ""}
      </div>
      <div class="kpis">
        <div class="kpi main"><span>Выручка</span><b>${fmtSom(s.revenue)} <small>сом</small></b>
          <div class="split" role="img" aria-label="Картой и QR ${fmtSom(card)} сом, наличными ${fmtSom(s.cash)} сом"><i class="qr" style="width:${share}%"></i><i class="cash" style="width:${100 - share}%"></i></div>
          <em><i class="sw qr"></i>картой и QR ${fmtSom(card)} · <i class="sw cash"></i>наличными ${fmtSom(s.cash)}</em></div>
        <div class="kpi"><span>Стаканов</span><b>${s.cups}</b><em>${s.cups ? "≈ " + fmtSom(Math.round(s.revenue / Math.max(1, s.cups))) + " сом за стакан" : "пока нет"}</em></div>
        <div class="kpi"><span>Бесплатных</span><b>${s.free}</b><em>подарено на ${fmtSom(s.freeCost)} сом</em></div>
        <div class="kpi"><span>Учеников с картой</span><b>${fmtSom(s.students)}</b><em>${s.newStudents ? "+" + s.newStudents + " за период" : "новых нет"}</em></div>
      </div>
      ${days > 1 ? `<section class="card chart-card">
        <div class="card-head"><span>Выручка по дням</span><button class="linkbtn" id="asTable">Таблицей</button></div>
        <div class="legend"><span><i class="sw qr"></i>Картой и QR — ${fmtSom(card)} сом</span><span><i class="sw cash"></i>Наличными — ${fmtSom(s.cash)} сом</span></div>
        <div id="chart"></div>
      </section>` : ""}
      <div class="two-col">
        <section class="card"><div class="card-head"><span>Чаще всех берут кофе</span></div>
          ${s.top.length ? `<ol class="rank">${s.top.map(t => `<li><b>${esc(t.name)}</b><span>${esc(t.grp)}</span><em>${t.cups}</em></li>`).join("")}</ol>` : `<p class="sub">Пока нет покупок за период.</p>`}</section>
        <section class="card"><div class="card-head"><span>Кто пробил стаканы</span></div>
          ${s.byStaff.length ? `<ol class="rank">${s.byStaff.map(t => `<li><b>${esc(t.name)}</b><span>${t.role === "system" ? "оплата в банке" : t.role === "admin" ? "администрация" : "бариста"}</span><em>${t.n} · ${fmtSom(t.sum)} сом</em></li>`).join("")}</ol>` : `<p class="sub">Пока нет операций.</p>`}</section>
      </div>
      ${s.pay === "demo" ? `<p class="note">${ico("gift", 16)}Включён демо-режим: продажи помечаются «демо» и не входят в выручку.</p>` : ""}`;
    host.querySelectorAll("[data-days]").forEach(b => b.onclick = () => admMoney(host, +b.dataset.days, test));
    const wt = host.querySelector("#wtest"); if (wt) wt.onchange = () => admMoney(host, days, wt.checked);
    const series = s.series.map(d => ({ day: d.day, a: (d.card || 0) + d.qr, b: d.cash, cups: d.cups }));
    const tb = host.querySelector("#asTable");
    if (tb) tb.onclick = () => {
      const c = host.querySelector("#chart"), on = tb.textContent === "Таблицей";
      tb.textContent = on ? "Графиком" : "Таблицей";
      c.innerHTML = on ? `<table class="tbl"><thead><tr><th>День</th><th>Картой и QR</th><th>Наличными</th><th>Стаканов</th></tr></thead><tbody>${
        series.slice().reverse().map(d => `<tr><td>${fmtDay(d.day)}</td><td>${fmtSom(d.a)}</td><td>${fmtSom(d.b)}</td><td>${d.cups}</td></tr>`).join("")}</tbody></table>` : barChart(series, c.clientWidth);
      bindChart(c);
    };
    const ch = host.querySelector("#chart");
    if (ch) { ch.innerHTML = barChart(series, ch.clientWidth); bindChart(ch); }
  }
  const fmtDay = d => new Date(d + "T00:00:00").toLocaleDateString("ru-RU", { day: "numeric", month: "short" });

  // Сложенные столбцы: снизу картой и QR (индиго), сверху наличные (карамель); зазор 2px, скруглён только верх.
  function barChart(series, width) {
    // Ширина — реальная ширина блока в пикселях: подписи осей остаются читаемыми и на телефоне.
    const W = Math.max(300, Math.round(width || 640)), H = W < 480 ? 200 : 240, pad = { l: 44, r: 8, t: 12, b: 26 }, n = series.length;
    const max = Math.max(100, ...series.map(d => d.a + d.b));
    const step = Math.pow(10, Math.floor(Math.log10(max))), top = Math.ceil(max / step) * step;
    const y = v => pad.t + (H - pad.t - pad.b) * (1 - v / top), bw = (W - pad.l - pad.r) / n, w = Math.max(4, Math.min(28, bw * .62));
    const grid = [0, .5, 1].map(k => `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(top * k)}" y2="${y(top * k)}"/><text x="${pad.l - 8}" y="${y(top * k) + 4}">${fmtSom(top * k)}</text>`).join("");
    const bar = (x, y0, y1, cls, round) => {
      const h = y0 - y1; if (h <= .5) return "";
      const r = round ? Math.min(4, w / 2, h) : 0;
      return `<path class="${cls}" d="M${x} ${y0}V${y1 + r}${r ? `q0 -${r} ${r} -${r}h${w - 2 * r}q${r} 0 ${r} ${r}` : `h${w}`}V${y0}Z"/>`;
    };
    const bars = series.map((d, i) => {
      const x = pad.l + bw * i + (bw - w) / 2, ya = y(d.a), yb = y(d.a + d.b);
      const label = i % Math.ceil(n / (W < 480 ? 4 : 8)) === 0 || i === n - 1 ? `<text class="xl" x="${x + w / 2}" y="${H - 8}">${fmtDay(d.day)}</text>` : "";
      return `<g class="bar" tabindex="0" data-tip="${fmtDay(d.day)}: картой и QR ${fmtSom(d.a)} · наличными ${fmtSom(d.b)} · ${d.cups} стак.">
        <rect class="hit" x="${pad.l + bw * i}" y="${pad.t}" width="${bw}" height="${H - pad.t - pad.b}"/>
        ${bar(x, y(0), ya, "qr", !d.b)}${bar(x, ya - (d.a && d.b ? 2 : 0), yb, "cash", true)}${label}</g>`;
    }).join("");
    return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Выручка по дням"><g class="grid">${grid}</g>${bars}</svg><div class="tip" hidden></div></div>`;
  }
  function bindChart(c) {
    const tip = c?.querySelector(".tip"); if (!tip) return;
    c.querySelectorAll(".bar").forEach(g => {
      const show = () => { // подсказка над столбцом, но не выходит за края блока
        const r = g.getBoundingClientRect(), b = c.getBoundingClientRect(); tip.textContent = g.dataset.tip; tip.hidden = false;
        const half = tip.offsetWidth / 2;
        tip.style.left = Math.min(b.width - half, Math.max(half, r.left - b.left + r.width / 2)) + "px"; g.classList.add("on"); };
      const hide = () => { tip.hidden = true; g.classList.remove("on"); };
      g.onpointerenter = show; g.onfocus = show; g.onpointerleave = hide; g.onblur = hide;
    });
  }

  // Ученики: поиск по имени, телефону, группе или коду; новый пароль и отключение.
  async function admStudents(host, q = state.sq || "", shown) {
    state.sq = q;
    if (!host.querySelector("#sq")) {
      host.innerHTML = `<form class="search" id="sfind" role="search">
          <label class="sr" for="sq">Поиск ученика</label>${ico("search")}
          <input id="sq" type="search" placeholder="Имя, телефон, группа или код карты" value="${esc(q)}" autocomplete="off" maxlength="40">
        </form><div id="slist"></div>`;
      let t;
      host.querySelector("#sq").oninput = e => { clearTimeout(t); t = setTimeout(() => admStudents(host, e.target.value.trim()), 280); };
      host.querySelector("#sfind").onsubmit = e => { e.preventDefault(); admStudents(host, host.querySelector("#sq").value.trim()); };
    }
    const list = host.querySelector("#slist");
    let r; try { r = await api("admin/students?q=" + encodeURIComponent(q)); } catch (x) { list.innerHTML = `<div class="empty">${esc(x.message)}</div>`; return; }
    list.innerHTML = `
      ${shown ? `<div class="secret card"><b>Новый пароль для ${esc(shown.name)}</b><p>Пароль <code>${esc(shown.password)}</code> — продиктуйте ученику. Вход по номеру телефона.</p></div>` : ""}
      <p class="sub count">${q ? `Найдено: ${r.students.length}${r.students.length === 50 ? "+" : ""}` : `Всего с картой: ${fmtSom(r.total)}${r.total > 50 ? " · показаны последние 50, остальных найдёт поиск" : ""}`}</p>
      ${r.students.length ? `<ul class="list students">${r.students.map(u => `<li class="${u.active ? "" : "undone"}">
        <span class="dot">${esc(u.name.slice(0, 1))}</span>
        <span class="what"><b>${esc(u.name)}</b><span>${esc(u.group)} · ${esc(u.phone)} · ${fmtCode(u.code)}${u.active ? "" : " · отключён"}</span></span>
        <span class="d" title="Баллов на карте">${u.points}</span>
        <button class="linkbtn" data-sreset="${u.id}" data-name="${esc(u.name)}">Пароль</button>
        <button class="linkbtn" data-stoggle="${u.id}">${u.active ? "Отключить" : "Включить"}</button></li>`).join("")}</ul>`
      : `<div class="empty">${q ? "Никого не нашли. Проверьте написание или поищите по номеру." : "Учеников пока нет."}</div>`}`;
    list.querySelectorAll("[data-sreset]").forEach(b => b.onclick = async () => {
      if (!confirm(`Выдать новый пароль для ${b.dataset.name}? Старый перестанет работать.`)) return;
      try { const x = await api(`admin/students/${b.dataset.sreset}/reset`, {}); admStudents(host, q, { name: b.dataset.name, password: x.password }); }
      catch (x) { toast(x.message, true); } });
    list.querySelectorAll("[data-stoggle]").forEach(b => b.onclick = async () => {
      try { await api(`admin/students/${b.dataset.stoggle}/toggle`, {}); admStudents(host, q); } catch (x) { toast(x.message, true); } });
  }

  async function admOrders(host) {
    host.innerHTML = `<div class="loading small">${ico("coffee", 32)}</div>`;
    let r; try { r = await api("admin/orders"); } catch (x) { host.innerHTML = `<div class="empty">${esc(x.message)}</div>`; return; }
    host.innerHTML = r.orders.length ? `<ul class="list orders">${r.orders.map(o => { const [t, c] = STATUS[o.status] || [o.status, "off"]; return `
      <li><span class="dot${o.free ? " free" : ""}">${o.num ? "№" + o.num : ico(o.free ? "gift" : "coffee", 18)}</span>
        <span class="what"><b>${esc(o.name)} · ${size(o.size).toLowerCase()}</b><span>${esc(o.group)} · ${fmtTime(o.created)} · ${o.free ? "за баллы" : o.provider === "gopay" ? "банк" : "на кассе"}${o.test ? " · демо" : ""}</span></span>
        <span class="st ${c}">${t}</span><span class="d">${o.free ? "0" : fmtSom(o.amount)}</span></li>`; }).join("")}</ul>`
      : `<div class="empty">Заказов ещё не было. Они появятся, когда ученики начнут заказывать с телефона.</div>`;
  }

  async function admStaff(host, shown) {
    let r; try { r = await api("admin/staff"); } catch (x) { host.innerHTML = `<div class="empty">${esc(x.message)}</div>`; return; }
    host.innerHTML = `
      ${shown ? `<div class="secret card"><b>Передайте сотруднику</b><p>Логин <code>${esc(shown.login)}</code> · пароль <code>${esc(shown.password)}</code></p>
        <p class="sub">Пароль показан один раз. Вход — по ссылке «Вход для сотрудников» внизу главной.</p>
        <button class="btn soft" id="copyPw">${ico("check", 16)}Скопировать</button></div>` : ""}
      <ul class="list staff">${r.staff.map(u => `<li class="${u.active ? "" : "undone"}">
        <span class="dot">${esc(u.name.slice(0, 1))}</span>
        <span class="what"><b>${esc(u.name)}${u.me ? " · вы" : ""}</b><span>${u.role === "admin" ? "Администрация" : "Бариста"} · ${esc(u.login)} · сегодня ${u.today} опер.${u.active ? "" : " · отключён"}</span></span>
        <button class="linkbtn" data-reset="${u.id}">Новый пароль</button>
        ${u.me ? "" : `<button class="linkbtn" data-toggle="${u.id}">${u.active ? "Отключить" : "Включить"}</button>`}</li>`).join("")}</ul>
      <section class="card"><div class="card-head"><span>Добавить сотрудника</span></div>
        <form class="form" id="addStaff" novalidate>
          <div class="two"><div class="field"><label for="n-name">Имя</label><input id="n-name" name="name" maxlength="60" required></div>
          <div class="field"><label for="n-login">Логин</label><input id="n-login" name="login" placeholder="aigerim" autocapitalize="none" spellcheck="false" maxlength="24" required></div></div>
          <div class="seg" role="radiogroup" aria-label="Роль"><button type="button" role="radio" aria-checked="true" data-role="staff">Бариста</button><button type="button" role="radio" aria-checked="false" data-role="admin">Администрация</button></div>
          <p class="err"></p><button class="btn" type="submit">Добавить и выдать пароль</button>
        </form></section>`;
    let role = "staff";
    host.querySelectorAll("[data-role]").forEach(b => b.onclick = () => { role = b.dataset.role; host.querySelectorAll("[data-role]").forEach(x => x.setAttribute("aria-checked", x === b)); });
    const cp = host.querySelector("#copyPw"); if (cp) cp.onclick = () => navigator.clipboard?.writeText(`Логин: ${shown.login}\nПароль: ${shown.password}`).then(() => toast("Скопировано"));
    host.querySelectorAll("[data-reset]").forEach(b => b.onclick = async () => {
      try { admStaff(host, await api(`admin/staff/${b.dataset.reset}/reset`, {})); } catch (x) { toast(x.message, true); } });
    host.querySelectorAll("[data-toggle]").forEach(b => b.onclick = async () => {
      try { await api(`admin/staff/${b.dataset.toggle}/toggle`, {}); admStaff(host); } catch (x) { toast(x.message, true); } });
    const f = host.querySelector("#addStaff");
    f.onsubmit = async e => {
      e.preventDefault();
      try { admStaff(host, await api("admin/staff", Object.assign(Object.fromEntries(new FormData(f)), { role }))); scrollTo({ top: 0, behavior: MOTION ? "smooth" : "auto" }); }
      catch (x) { f.querySelector(".err").textContent = x.message; }
    };
  }

  function admSettings(host) {
    const p = RULES.prices, mode = RULES.pay;
    host.innerHTML = `
      <section class="card"><div class="card-head"><span>Цены и баллы</span></div>
        <form class="form" id="rules" novalidate>
          <div class="two"><div class="field"><label for="r-s">Маленький, сом</label><input id="r-s" name="price_s" inputmode="numeric" value="${p.s}"></div>
          <div class="field"><label for="r-l">Большой, сом</label><input id="r-l" name="price_l" inputmode="numeric" value="${p.l}"></div></div>
          <div class="two"><div class="field"><label for="r-c">Баллов за стакан</label><input id="r-c" name="per_cup" inputmode="numeric" value="${RULES.perCup}"></div>
          <div class="field"><label for="r-f">Баллов за бесплатный</label><input id="r-f" name="free_cost" inputmode="numeric" value="${RULES.freeCost}"></div></div>
          <p class="err"></p><button class="btn" type="submit">Сохранить</button>
        </form></section>
      <section class="card pay-status"><div class="card-head"><span>Как принимаются деньги</span>
          <span class="st ${mode === "gopay" ? "ok" : DESK(mode) ? "ok" : "off"}">${mode === "gopay" ? "Касса + банк" : mode === "demo" ? "Демо" : DESK(mode) ? "На кассе" : "Выключено"}</span></div>
        <p class="sub">${DESK(mode) || mode === "gopay"
          ? "Ученик заказывает в телефоне и показывает QR. Бариста сканирует, принимает наличные или карту и нажимает «выдать» — тогда засчитываются и деньги, и баллы."
          : "Заказ с телефона выключен — ученики платят на кассе, бариста ставит оттиск по QR."}
          ${mode === "demo" ? " Сейчас включён демо-режим: продажи не входят в выручку." : ""}</p>
        ${mode !== "gopay" ? `<p class="sub">Можно добавить оплату по QR банка (GoPay, ELQR — MBank, MegaPay, Оптима и другие):</p>
          <ol class="steps"><li>Зарегистрировать кофейню в <a href="https://merchant.gopay.kg/signup" target="_blank" rel="noopener">GoPay</a> (ИП или ОсОО, комиссия 1–2%).</li>
            <li>В кабинете GoPay: «Разработчику» → API-ключ и секрет; в «Вебхуки» — адрес<br><code>https://mukrcoffee.62-113-109-193.sslip.io/api/pay/gopay</code> и событие <code>payment.committed</code>.</li>
            <li>Передать ключи разработчику — он внесёт их в настройки сервера.</li></ol>` : ""}
      </section>`;
    const f = host.querySelector("#rules");
    f.onsubmit = async e => {
      e.preventDefault();
      try { RULES = await api("admin/settings", Object.fromEntries(new FormData(f))); toast("Сохранено"); f.querySelector(".err").textContent = ""; }
      catch (x) { f.querySelector(".err").textContent = x.message; }
    };
  }

  // ---------- общее ----------
  async function logout() {
    stopCam(); clearInterval(pollTimer); closeSheet();
    try { await api("logout", {}); } catch {}
    store.tok = ""; state = fresh();
    render();
  }

  function draw() {
    viewId++;
    stopCam(); clearInterval(pollTimer); closeSheet(); queueSeen = null;
    document.body.dataset.role = state.user?.role || "guest";
    if (!state.user) return location.hash === "#staff" ? viewStaffLogin() : viewLanding();
    if (state.user.role === "admin") return viewAdmin();
    if (state.user.role === "student") {
      state.lastPts = null; state.lastOp = 0;
      viewStudent(true); refreshMe();
      // Пока карта открыта, следим за баллами: бариста отсканировал — оттиск появляется сам.
      pollTimer = setInterval(() => document.visibilityState === "visible" && !sheet && refreshMe(), 5000);
    } else {
      viewDesk();
      // Очередь проверяем часто — новый заказ должен появиться у бариста сразу.
      pollTimer = setInterval(() => { if (document.visibilityState === "visible") { loadQueue(); loadToday(); } }, 4000);
    }
  }
  // Смена экрана (вход, выход) — плавным переходом, где браузер умеет.
  function render() {
    if (MOTION && document.startViewTransition && $app.firstElementChild?.className !== "loading") document.startViewTransition(draw);
    else draw();
  }

  // Башня на фоне: при прокрутке уходит вверх медленнее страницы — даёт глубину.
  function backdrop() {
    const bg = document.querySelector(".bg");
    bg.insertAdjacentHTML("beforeend", `<svg class="spire" viewBox="12 0 36 116" aria-hidden="true">${tower("", "currentColor", "#F1F1FA")}</svg>`);
    if (!MOTION) return;
    const sp = bg.querySelector(".spire");
    let raf = 0;
    addEventListener("scroll", () => {
      if (!raf) raf = requestAnimationFrame(() => { raf = 0; sp.style.transform = `translate3d(0, ${-scrollY * .12}px, 0)`; });
    }, { passive: true });
  }

  async function boot() {
    backdrop();
    $app.innerHTML = `<div class="loading">${ico("coffee", 44)}</div>`;
    const rules = api("rules").then(r => { RULES = r; }).catch(() => {});
    if (store.tok) {
      try { const me = await api("me"); state.user = me.user; state.me = me; } catch { }
    }
    await rules;
    render();
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state.user?.role === "student" && !sheet) refreshMe();
  });
  addEventListener("hashchange", () => { if (!state.user) render(); });
  boot();
})();
