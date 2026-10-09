/* KMARKET — интерфейс.
 *
 * Никаких фреймворков и сборки: страница открывается напрямую из файла.
 * Всё общение с Python — через pywebview.api.<метод>(), события прилетают
 * обратно в window.kmarket.emit().
 *
 * УСТРОЙСТВО (упрощение 2026-10-09). Слева список того, за чем следим:
 * жетон и товары, выбранные Кареном. Справа один и тот же разбор для
 * любого из них. Отчёты приходят событиями по мере готовности и лежат в
 * state.reports по ключу ("token" или id предмета).
 *
 * ПРО ДВИЖЕНИЕ. Его нет: система KTRANS запрещает keyframes и transform,
 * переходы только по цвету.
 */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  region: "eu",
  days: 30,
  key: "token",
  assets: [],
  reports: {},
  confirmUntrack: false,
};

/* ---------- утилиты ---------- */

// Пробел неразрывный: цена не должна переноситься посреди числа.
const gold = (n) => Math.round(n).toLocaleString("ru-RU").replace(/\s/g, " ");

// Товары часто стоят десятки золотых, жетон — сотни тысяч. Один формат на
// всё врал бы в обе стороны: «0 з» у травы и «1 234,57 з» у самоцвета.
const goldFine = (n) => {
  if (n >= 100) return gold(n);
  if (n >= 1) return n.toFixed(1).replace(".", ",");
  return n.toFixed(2).replace(".", ",");
};

const signed = (n) => (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(n).toFixed(1).replace(".", ",") + "%";

const isToken = () => state.key === "token";

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s == null ? "" : String(s);
  return d.innerHTML;
}

const MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
const shortDate = (iso) => {
  const d = new Date(iso.length === 10 ? iso + "T00:00:00Z" : iso);
  return d.getDate() + " " + MONTHS[d.getMonth()];
};

const STATE_WORDS = {
  buy: "брать",
  wait: "ждать",
  hold: "ждать",
  avoid: "не сейчас",
  sell: "продавать",
};

function showError(text) {
  const box = $("#error");
  box.textContent = text;
  box.hidden = false;
}

let toastTimer = null;
function toast(text) {
  const box = $("#toast");
  box.textContent = text;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (box.hidden = true), 6000);
}

$("#error").addEventListener("click", () => ($("#error").hidden = true));
$("#toast").addEventListener("click", () => ($("#toast").hidden = true));

/* ---------- шапка окна ---------- */

$$(".win").forEach((btn) =>
  btn.addEventListener("click", async () => {
    const maximized = await window.pywebview.api.window_action(btn.dataset.win);
    document.body.dataset.max = maximized ? "1" : "0";
  })
);

// Двойной щелчок по шапке разворачивает окно: frameless отключает
// системное изменение размера.
$(".titlebar").addEventListener("dblclick", async (e) => {
  if (e.target.closest(".win")) return;
  const maximized = await window.pywebview.api.window_action("maximize");
  document.body.dataset.max = maximized ? "1" : "0";
});

/* ---------- левая колонка ---------- */

function iconHtml(asset, cls = "icon") {
  if (asset.key === "token") return `<span class="${cls} token"></span>`;
  return asset.icon
    ? `<img class="${cls}" src="${asset.icon}" alt="">`
    : `<span class="${cls}"></span>`;
}

function renderAssets() {
  const searching = $('.view[data-view="search"]').classList.contains("is-on");
  $("#assets").innerHTML = state.assets
    .map((a) => {
      const r = state.reports[a.key];
      let sub = "считаю…";
      if (r && r.empty) sub = "истории нет";
      else if (r) {
        const price = a.key === "token" ? gold(r.current.price) : goldFine(r.current.price);
        sub = `${price} з · <span class="st" data-state="${r.verdict.state}">${
          STATE_WORDS[r.verdict.state] || ""
        }</span>`;
      }
      const on = !searching && a.key === state.key ? " is-on" : "";
      return `<button class="asset${on}" data-key="${a.key}" type="button">
        ${iconHtml(a)}
        <span class="iname">${escapeHtml(a.name)}<span class="isub">${sub}</span></span>
      </button>`;
    })
    .join("");
  $$(".asset").forEach((btn) => btn.addEventListener("click", () => select(btn.dataset.key)));
  $("#add").classList.toggle("is-on", searching);
}

function showView(name) {
  $$(".view").forEach((v) => v.classList.toggle("is-on", v.dataset.view === name));
  renderAssets();
}

function select(key) {
  state.key = key;
  state.confirmUntrack = false;
  showView("asset");
  renderAsset();
}

$("#add").addEventListener("click", () => {
  showView("search");
  $("#q").focus();
});

$("#refresh").addEventListener("click", () => {
  $("#stamp").textContent = "спрашиваю Blizzard…";
  window.pywebview.api.refresh(state.region);
});

/* ---------- регион (только у жетона) ---------- */

$$("#regions .tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    if (state.region === tab.dataset.region) return;
    state.region = tab.dataset.region;
    markRegion();
    delete state.reports.token;
    renderAsset();
    window.pywebview.api.start_token(state.region);
  })
);

function markRegion() {
  $$("#regions .tab").forEach((t) => t.classList.toggle("is-on", t.dataset.region === state.region));
}

/* ТОЛЬКО КНОПКИ ВНУТРИ #ranges. Класс .range носят и другие кнопки
 * («обновить», «не следить»); выборка по одному классу однажды уводила
 * state.days в NaN, и график переставал перерисовываться. */
$$("#ranges .range").forEach((btn) =>
  btn.addEventListener("click", () => {
    $$("#ranges .range").forEach((b) => b.classList.toggle("is-on", b === btn));
    state.days = Number(btn.dataset.days);
    drawChart();
  })
);

/* ---------- разбор ---------- */

function renderAsset() {
  const asset = state.assets.find((a) => a.key === state.key) || state.assets[0];
  if (!asset) return;
  const r = state.reports[asset.key];
  const token = asset.key === "token";

  $("#a-icon").outerHTML = iconHtml(asset, "icon big").replace('class="', 'id="a-icon" class="');
  $("#a-name").textContent = asset.name;
  $("#regions").hidden = !token;
  const untrack = $("#untrack");
  untrack.hidden = token;
  untrack.textContent = "не следить";

  const parts = [];
  if (!token && asset.subclass) parts.push(asset.subclass);
  if (r && !r.empty && !token) parts.push(`самый дешёвый лот ${goldFine(r.current.floor)} з`);
  if (token) parts.push(state.region.toUpperCase() + " · покупаю за золото");
  $("#a-sub").textContent = parts.join(" · ");

  if (!r) {
    $("#v-state").textContent = "…";
    $("#v-state").dataset.state = "";
    $("#v-price").textContent = "—";
    $("#v-summary").textContent = "Считаю историю…";
    $("#v-reasons").innerHTML = "";
    $("#windows").innerHTML = "";
    $("#rhythm").innerHTML = "";
    $("#cycle").innerHTML = "";
    $("#chart").innerHTML = "";
    return;
  }
  if (r.empty) {
    $("#v-state").textContent = "НЕТ ДАННЫХ";
    $("#v-state").dataset.state = "";
    $("#v-price").textContent = "—";
    $("#v-summary").textContent =
      "Истории пока нет. Облако начнёт записывать товар со следующего часового снимка.";
    $("#v-reasons").innerHTML = "";
    $("#windows").innerHTML = "";
    $("#rhythm").innerHTML = "";
    $("#cycle").innerHTML = "";
    $("#chart").innerHTML = "";
    return;
  }

  const v = r.verdict;
  $("#v-state").textContent = v.title;
  $("#v-state").dataset.state = v.state;
  $("#v-price").innerHTML =
    (token ? gold(r.current.price) : goldFine(r.current.price)) + ' <span class="unit">з</span>';
  $("#v-summary").textContent = v.summary + " · уверенность " + v.confidence;
  $("#v-reasons").innerHTML = v.reasons.map((x) => "<li>" + escapeHtml(x) + "</li>").join("");

  const fmt = token ? gold : goldFine;
  const cells = r.windows.map(
    (w) => `<div class="cell">
      <b>${w.percentile.toFixed(0)}</b>
      <span>перцентиль · ${escapeHtml(w.label)} · ${fmt(w.low)}–${fmt(w.high)} з</span>
    </div>`
  );
  const age = r.current.age_minutes;
  const fresh = age < 90 ? age + " мин" : Math.round(age / 60) + " ч";
  cells.push(`<div class="cell">
    <b>${r.history.points.toLocaleString("ru-RU")}</b>
    <span>замеров с ${shortDate(r.history.since)}${
      new Date(r.history.since).getFullYear() !== new Date().getFullYear()
        ? " " + new Date(r.history.since).getFullYear()
        : ""
    } · свежесть ${fresh}</span>
  </div>`);
  $("#windows").innerHTML = cells.join("");

  renderRhythm(r.rhythm);
  renderCycle(r);
  drawChart();
}

/* Недельный ритм. У жетона он измерен на годе данных, у товаров — на
 * паре месяцев, и это обязано быть видно: рисунок по четырём замерам на
 * клетку легко принять за расписание, которого нет. */
function renderRhythm(rh) {
  const box = $("#rhythm");
  if (!rh || !rh.cheapest || !rh.cheapest.length) {
    box.innerHTML = `<div class="note">Истории пока мало, чтобы увидеть недельный рисунок.</div>`;
    return;
  }
  const cell = (c) => `${c.weekday} ${String(c.hour).padStart(2, "0")}:00`;
  const buy = rh.cheapest[0];
  const sell = rh.dearest[0];
  const days = Object.entries(rh.by_weekday || {})
    .map(([d, x]) => `<span class="wd"><b>${d}</b> ${signed(x)}</span>`)
    .join("");
  box.innerHTML = `
    <div class="rhythm">
      <div><span class="rh-when">${cell(buy)}</span><span class="rh-what">дешевле всего · ${signed(buy.deviation)}</span></div>
      <div><span class="rh-when">${cell(sell)}</span><span class="rh-what">дороже всего · ${signed(sell.deviation)}</span></div>
    </div>
    <div class="weekdays">${days}</div>
    <div class="caveat">${
      rh.reliable
        ? `Разброс внутри недели ${rh.spread_pct.toFixed(1).replace(".", ",")}%, ${rh.points.toLocaleString("ru-RU")} замеров — рисунок устойчивый.`
        : `Разброс ${rh.spread_pct.toFixed(1).replace(".", ",")}%, но замеров всего ${rh.points.toLocaleString("ru-RU")} — это первые наброски, а не расписание.`
    }</div>`;
}

/* Цикл контента — то, ради чего проект вообще существует: перед патчем
 * дорого, после дёшево. У товара показываем его собственный прошлый цикл,
 * у жетона — эффект, измеренный на всех событиях с 2020 года. */
function renderCycle(r) {
  const box = $("#cycle");
  const parts = [];
  const up = (r.events && r.events.upcoming) || [];

  if (r.kind === "item" && r.cycle) {
    const c = r.cycle;
    const fmt = goldFine;
    parts.push(`<div class="cyc-title">${escapeHtml(c.label)}</div>
      <div class="cyc">
        <div><b>${fmt(c.before)}</b><span>за неделю до</span></div>
        <div><b>${fmt(c.peak)}</b><span>пик · ${shortDate(c.peak_date)} · ${signed(c.rise_pct)}</span></div>
        <div><b>${fmt(c.low)}</b><span>дно · ${shortDate(c.low_date)}</span></div>
        <div><b>${fmt(c.now)}</b><span>сейчас · ${signed(c.fall_pct)} от пика</span></div>
      </div>`);
  } else if (r.kind === "item") {
    parts.push(`<div class="note">Ни одного патча или сезона в истории товара пока нет —
      сравнивать не с чем.</div>`);
  } else {
    const all = ((r.events && r.events.studies) || []).find((s) => s.kind === "all");
    if (all) {
      parts.push(`<div class="cyc">
        <div><b>${signed(all.before_pct)}</b><span>за 30–8 дней до</span></div>
        <div><b>${signed(all.around_pct)}</b><span>±7 дней</span></div>
        <div><b>${signed(all.after_pct)}</b><span>через 8–30 дней</span></div>
      </div>
      <div class="caveat">Измерено на ${all.events} событиях с 2020 года, к медиане
        цены вокруг каждого.</div>`);
    }
  }

  if (up.length) {
    parts.push(
      `<div class="next">` +
        up.map((e) => `<div><b>${escapeHtml(e.label)}</b> — ${shortDate(e.date)}, через ${e.in_days} дн</div>`).join("") +
        `</div>`
    );
  } else {
    parts.push(`<div class="caveat">Будущих дат в календаре нет: фаза «до» — самый сильный
      сигнал — не видна, пока следующий патч или сезон не внесён руками.</div>`);
  }
  box.innerHTML = parts.join("");
}

/* ---------- не следить ---------- */

$("#untrack").addEventListener("click", () => {
  const btn = $("#untrack");
  // Двойной клик-подтверждение: убрать можно и случайно, а облако
  // перестанет писать товар сразу (история при этом остаётся).
  if (!state.confirmUntrack) {
    state.confirmUntrack = true;
    btn.textContent = "точно? нажми ещё раз";
    return;
  }
  state.confirmUntrack = false;
  btn.textContent = "отправляю…";
  window.pywebview.api.untrack(Number(state.key));
});

/* ---------- график ---------- */

async function drawChart() {
  const key = state.key;
  const res = await window.pywebview.api.get_chart(key, state.region, state.days);
  if (key !== state.key) return; // пока считали, человек ушёл на другой товар
  const svg = $("#chart");
  if (!res || res.error || !res.points || res.points.length < 2) {
    svg.innerHTML = "";
    state.probe = null;
    return;
  }

  const W = 1000;
  const H = 190;
  const padL = 6;
  const padR = 58; // место под подпись последней цены
  const padY = 14;
  const fmt = isToken() ? gold : goldFine;

  const values = res.points.map((p) => p[1]);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;

  const x = (i) => padL + (i / (res.points.length - 1)) * (W - padL - padR);
  const y = (v) => padY + (1 - (v - lo) / span) * (H - padY * 2);

  const d = res.points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");

  // Отметки событий: вертикальный пунктир там, где патч или старт сезона.
  const times = res.points.map((p) => new Date(p[0]).getTime());
  const marks = (res.events || [])
    .map((ev) => {
      const t = new Date(ev.date + "T00:00:00Z").getTime();
      const idx = times.findIndex((v) => v >= t);
      if (idx < 1) return "";
      const px = x(idx).toFixed(1);
      return `<line class="evt" x1="${px}" y1="${padY}" x2="${px}" y2="${H - padY}"><title>${escapeHtml(ev.label)}</title></line>`;
    })
    .join("");

  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = `
    <line class="axis" x1="${padL}" y1="${y(hi).toFixed(1)}" x2="${W - padR}" y2="${y(hi).toFixed(1)}"/>
    <line class="axis" x1="${padL}" y1="${y(lo).toFixed(1)}" x2="${W - padR}" y2="${y(lo).toFixed(1)}"/>
    ${marks}
    <path class="line" d="${d}"/>
    <text x="${W - padR + 6}" y="${y(hi) + 4}">${fmt(hi)}</text>
    <text x="${W - padR + 6}" y="${y(lo) + 4}">${fmt(lo)}</text>
    <g id="probe" style="display:none">
      <line class="probe-line" y1="${padY}" y2="${H - padY}"/>
      <circle class="probe-dot" r="3"/>
    </g>
  `;
  state.probe = { points: res.points, x, y, W, padL, padR, fmt };
  bindProbe();
}

/* Наведение на график: крестик, точка и значение.
 *
 * viewBox растянут на всю ширину карточки, поэтому экранные пиксели надо
 * переводить в координаты viewBox — отсюда пересчёт через getBoundingClientRect. */
function bindProbe() {
  const svg = $("#chart");
  const box = $("#probe-readout");
  if (svg.dataset.bound) return;
  svg.dataset.bound = "1";

  svg.addEventListener("mousemove", (e) => {
    const p = state.probe;
    if (!p || !p.points.length) return;
    const rect = svg.getBoundingClientRect();
    const vx = ((e.clientX - rect.left) / rect.width) * p.W;
    const usable = p.W - p.padL - p.padR;
    let idx = Math.round(((vx - p.padL) / usable) * (p.points.length - 1));
    idx = Math.max(0, Math.min(p.points.length - 1, idx));

    const [when, price] = p.points[idx];
    const px = p.x(idx);
    const probe = svg.querySelector("#probe");
    probe.style.display = "";
    probe.querySelector("line").setAttribute("x1", px);
    probe.querySelector("line").setAttribute("x2", px);
    probe.querySelector("circle").setAttribute("cx", px);
    probe.querySelector("circle").setAttribute("cy", p.y(price));

    const at = new Date(when);
    const long = state.days >= 45;
    box.textContent =
      at.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) +
      (long ? "" : ", " + at.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })) +
      " — " + p.fmt(price) + " з" +
      (long ? " (медиана за день)" : "");
    box.hidden = false;
  });

  svg.addEventListener("mouseleave", () => {
    const probe = svg.querySelector("#probe");
    if (probe) probe.style.display = "none";
    box.hidden = true;
  });
}

/* ---------- поиск ---------- */

$("#search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("#q").value.trim();
  if (q.length < 2) return;
  $("#search-note").textContent = "Ищу «" + q + "»…";
  $("#results").hidden = true;
  window.pywebview.api.search(q);
});

function renderResults(ev) {
  const note = $("#search-note");
  const box = $("#results");
  if (ev.error) {
    note.textContent = "Поиск не удался: " + ev.error;
    box.hidden = true;
    return;
  }
  if (!ev.results.length) {
    note.textContent = ev.total
      ? `Нашлось ${ev.total}, но ни один не торгуется на товарном аукционе прямо сейчас.`
      : "Ничего не нашлось. Попробуй часть названия.";
    box.hidden = true;
    return;
  }
  note.textContent =
    "Цена — рынок, граница нижних 15% предложения. «История» — с какого дня облако уже пишет товар.";
  box.hidden = false;
  box.innerHTML =
    `<div class="row head"><span></span><span>Товар</span><span class="num">Цена</span>
       <span class="num">История</span><span></span></div>` +
    ev.results
      .map(
        (it) => `<div class="row">
        ${iconHtml(it)}
        <span class="iname">${escapeHtml(it.name)}
          <span class="isub">${escapeHtml([it.quality, it.subclass].filter(Boolean).join(" · "))}</span></span>
        <span class="num">${goldFine(it.price)} з</span>
        <span class="num soft">${it.since ? "с " + shortDate(it.since) : "нет"}</span>
        <span class="num">${
          it.tracked
            ? `<span class="soft">уже слежу</span>`
            : `<button class="range follow" data-id="${it.id}" type="button">следить</button>`
        }</span>
      </div>`
      )
      .join("");
  $$(".follow").forEach((btn) =>
    btn.addEventListener("click", () => {
      btn.textContent = "отправляю…";
      btn.disabled = true;
      window.pywebview.api.track(Number(btn.dataset.id));
    })
  );
}

/* ---------- события из Python ---------- */

window.kmarket = {
  emit(event) {
    if (event.type === "report") {
      state.reports[event.key] = event.data;
      if (event.data.meta) {
        const i = state.assets.findIndex((a) => a.key === event.key);
        if (i >= 0) state.assets[i] = event.data.meta;
      }
      renderAssets();
      if (event.key === state.key) renderAsset();
      $("#stamp").textContent = "обновлено " + new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    } else if (event.type === "search") {
      renderResults(event);
    } else if (event.type === "tracked") {
      if (!event.ok) {
        showError(event.text);
        $$(".follow").forEach((b) => ((b.disabled = false), (b.textContent = "следить")));
        $("#untrack").textContent = "не следить";
        return;
      }
      state.assets = event.assets;
      toast(event.text);
      if (event.added) select(event.key);
      else {
        delete state.reports[event.key];
        select("token");
      }
    } else if (event.type === "note") {
      toast(event.text);
    } else if (event.type === "error") {
      showError(event.text);
    }
  },
};

window.addEventListener("pywebviewready", async () => {
  const boot = await window.pywebview.api.bootstrap();
  state.region = boot.primary || "eu";
  state.assets = boot.assets || [];
  markRegion();
  renderAssets();
  renderAsset();
  window.pywebview.api.start_load(state.region);
});
