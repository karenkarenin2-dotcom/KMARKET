/* KMARKET — интерфейс.
 *
 * Никаких фреймворков и сборки: страница открывается напрямую из файла.
 * Всё общение с Python — через pywebview.api.<метод>(), события прилетают
 * обратно в window.kmarket.emit().
 *
 * ПРО ДВИЖЕНИЕ. Его нет: система KTRANS запрещает keyframes и transform,
 * переходы только по цвету. График перерисовывается целиком, без анимации
 * — данные приходят раз в двадцать минут, сглаживать нечего.
 */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  region: "eu",
  days: 90,
  report: null,
  auction: null,
  ready: false,
};

/* ---------- утилиты ---------- */

// Пробел неразрывный: цена не должна переноситься посреди числа.
const gold = (n) =>
  Math.round(n).toLocaleString("ru-RU").replace(/ |\s/g, " ");

// Реагенты часто стоят меньше золотого, поэтому мелочь показываем с сотыми,
// а крупное — целым. Один и тот же формат на всё врал бы в обе стороны:
// «0 з» у травы и «1 234,57 з» у эпического самоцвета.
const goldFine = (n) => {
  if (n >= 100) return gold(n);
  if (n >= 1) return n.toFixed(1).replace(".", ",");
  return n.toFixed(2).replace(".", ",");
};

const pct = (n) => (n > 0 ? "+" : "") + n.toFixed(1).replace(".", ",") + "%";

function showError(text) {
  const box = $("#error");
  box.textContent = text;
  box.hidden = false;
}

$("#error").addEventListener("click", () => ($("#error").hidden = true));

/* ---------- шапка окна ---------- */

$$(".win").forEach((btn) =>
  btn.addEventListener("click", async () => {
    const maximized = await window.pywebview.api.window_action(btn.dataset.win);
    document.body.dataset.max = maximized ? "1" : "0";
  })
);

// Двойной щелчок по шапке разворачивает окно: frameless отключает
// системное изменение размера, и без этого длинный список неудобно читать.
$(".titlebar").addEventListener("dblclick", async (e) => {
  if (e.target.closest(".win")) return;
  const maximized = await window.pywebview.api.window_action("maximize");
  document.body.dataset.max = maximized ? "1" : "0";
});

/* ---------- вкладки ---------- */

$$(".tab[data-tab]").forEach((tab) =>
  tab.addEventListener("click", () => {
    $$(".tab[data-tab]").forEach((t) => t.classList.toggle("is-on", t === tab));
    $$(".view").forEach((v) =>
      v.classList.toggle("is-on", v.dataset.view === tab.dataset.tab)
    );
    if (tab.dataset.tab === "auction") loadAuction();
  })
);

$$(".tab[data-region]").forEach((tab) =>
  tab.addEventListener("click", () => {
    if (state.region === tab.dataset.region) return;
    state.region = tab.dataset.region;
    markRegion();
    reload();
  })
);

function markRegion() {
  $$(".tab[data-region]").forEach((t) =>
    t.classList.toggle("is-on", t.dataset.region === state.region)
  );
}

$$(".range").forEach((btn) =>
  btn.addEventListener("click", () => {
    $$(".range").forEach((b) => b.classList.toggle("is-on", b === btn));
    state.days = Number(btn.dataset.days);
    drawChart();
  })
);

/* ---------- вердикт ---------- */

function renderReport(data) {
  state.report = data;
  if (data.empty) {
    $("#v-state").textContent = "НЕТ ДАННЫХ";
    $("#v-summary").textContent = "История пуста — сначала должен отработать сборщик.";
    return;
  }

  const v = data.verdict;
  const el = $("#v-state");
  el.textContent = v.title;
  el.dataset.state = v.state;

  $("#v-price").innerHTML =
    gold(data.current.price) + ' <span class="unit">g</span>';
  $("#v-summary").textContent = v.summary + " · уверенность " + v.confidence;
  $("#v-reasons").innerHTML = v.reasons
    .map((r) => "<li>" + escapeHtml(r) + "</li>")
    .join("");

  const cells = data.windows.map(
    (w) => `<div class="cell">
      <b>${w.percentile.toFixed(0)}</b>
      <span>перцентиль · ${escapeHtml(w.label)} · ${gold(w.low)}–${gold(w.high)} g</span>
    </div>`
  );
  const age = data.current.age_minutes;
  cells.push(`<div class="cell">
    <b>${data.history.points.toLocaleString("ru-RU")}</b>
    <span>точек в истории · свежесть ${age < 90 ? age + " мин" : Math.round(age / 60) + " ч"}</span>
  </div>`);
  $("#windows").innerHTML = cells.join("");

  $("#stamp").textContent =
    "обновлено " + new Date(data.generated_at).toLocaleTimeString("ru-RU");
  drawChart();
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

/* ---------- график ---------- */

async function drawChart() {
  const res = await window.pywebview.api.get_chart(state.region, state.days);
  const svg = $("#chart");
  if (!res || res.error || !res.points || res.points.length < 2) {
    svg.innerHTML = "";
    return;
  }

  const W = 1000;
  const H = 190;
  const padL = 6;
  const padR = 58; // место под подпись последней цены
  const padY = 14;

  const values = res.points.map((p) => p[1]);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;

  const x = (i) =>
    padL + (i / (res.points.length - 1)) * (W - padL - padR);
  const y = (v) => padY + (1 - (v - lo) / span) * (H - padY * 2);

  const d = res.points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");

  // Отметки событий: вертикальный пунктир там, где патч или старт сезона.
  const times = res.points.map((p) => new Date(p[0]).getTime());
  const marks = (res.events || [])
    .map((ev) => {
      const t = new Date(ev.date + "T00:00:00Z").getTime();
      let idx = times.findIndex((v) => v >= t);
      if (idx < 1) return "";
      const px = x(idx).toFixed(1);
      return `<line class="evt" x1="${px}" y1="${padY}" x2="${px}" y2="${H - padY}"/>`;
    })
    .join("");

  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = `
    <line class="axis" x1="${padL}" y1="${y(hi).toFixed(1)}" x2="${W - padR}" y2="${y(hi).toFixed(1)}"/>
    <line class="axis" x1="${padL}" y1="${y(lo).toFixed(1)}" x2="${W - padR}" y2="${y(lo).toFixed(1)}"/>
    ${marks}
    <path class="line" d="${d}"/>
    <text x="${W - padR + 6}" y="${y(hi) + 4}">${gold(hi)}</text>
    <text x="${W - padR + 6}" y="${y(lo) + 4}">${gold(lo)}</text>
    <g id="probe" style="display:none">
      <line class="probe-line" y1="${padY}" y2="${H - padY}"/>
      <circle class="probe-dot" r="3"/>
    </g>
  `;

  // Читаем точное значение под курсором. Держим данные и пересчёт
  // координат прямо здесь: искать ближайшую точку надо в тех же
  // единицах, в которых её нарисовали, иначе подпись разъедется с линией.
  state.probe = { points: res.points, x, y, W, H, padL, padR };
  bindProbe();
}

/* Наведение на график: крестик, точка и значение.
 *
 * viewBox растянут на всю ширину карточки, поэтому экранные пиксели надо
 * переводить в координаты viewBox — отсюда пересчёт через getBoundingClientRect,
 * а не прямое использование offsetX. */
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

    // Ближайшая точка по горизонтали.
    const usable = p.W - p.padL - p.padR;
    let idx = Math.round(((vx - p.padL) / usable) * (p.points.length - 1));
    idx = Math.max(0, Math.min(p.points.length - 1, idx));

    const [when, price] = p.points[idx];
    const px = p.x(idx);
    const py = p.y(price);

    const probe = svg.querySelector("#probe");
    probe.style.display = "";
    probe.querySelector("line").setAttribute("x1", px);
    probe.querySelector("line").setAttribute("x2", px);
    probe.querySelector("circle").setAttribute("cx", px);
    probe.querySelector("circle").setAttribute("cy", py);

    const when_ = new Date(when);
    const long = state.days >= 45;
    box.textContent =
      when_.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) +
      (long ? "" : ", " + when_.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })) +
      " — " + gold(price) + " g" +
      (long ? " (медиана за день)" : "");
    box.hidden = false;
  });

  svg.addEventListener("mouseleave", () => {
    const probe = svg.querySelector("#probe");
    if (probe) probe.style.display = "none";
    box.hidden = true;
  });
}

/* ---------- аукцион ---------- */

async function loadAuction() {
  if (state.auction || !state.ready) return;
  const data = await window.pywebview.api.get_auction();
  state.auction = data;
  renderAuction(data);
}

function renderAuction(data) {
  const note = $("#a-note");
  if (!data || !data.items || !data.items.length) {
    note.textContent =
      "Истории аукциона пока нет. Сборщик начнёт наполнять её после первого запуска " +
      "workflow «auction», а список слежки собирается командой python -m kmarket.watchlist.";
    $("#a-rows").innerHTML = "";
    return;
  }

  const ev = data.event;
  const when = data.live_utc
    ? "Цены на " +
      new Date(data.live_utc).toLocaleString("ru-RU", {
        day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
      }) +
      " — так их отдаёт Blizzard, обновляются раз в час."
    : data.stale
    ? "Живой снимок не получен, показываю последний сохранённый."
    : "";
  note.textContent =
    `${when} Под слежкой ${data.tracked} товаров.` +
    (ev
      ? ` Ближайшее событие: ${ev.label}, ${
          ev.days > 0 ? "через " + ev.days + " дн" : Math.abs(ev.days) + " дн назад"
        }.`
      : "");

  renderReadiness(data.readiness);

  const bargains = data.bargains || [];
  $("#a-bargains").innerHTML = bargains.length
    ? `<div class="label" style="padding:6px 0 8px">Недооценённые прямо сейчас</div>
       <div class="rows">${headRow()}${bargains.map(row).join("")}</div>
       <div style="height:12px"></div>`
    : "";

  $("#a-rows").innerHTML = headRow() + data.items.map(row).join("");
}

/* Подписи колонок намеренно НЕ жаргонные. «Пол» и «уровень» — слова из
 * биржевого стакана, и на вопрос «это цена или не цена?» они не отвечают.
 * Пишем то, что человек увидит в игре. */
/* Полоса созревания. Существует ради одного вопроса, который иначе
 * пришлось бы задавать вслух: «а скорость товара уже работает?».
 * Приложение обязано отвечать на него само. */
function renderReadiness(r) {
  const box = $("#a-ready");
  if (!r || !r.total) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const hours = r.points;
  const bit = (done, need, what) =>
    done
      ? `<b>${what} — работает</b> (${done} из ${r.total} товаров)`
      : `${what} — нужно ${need} снимков, есть ${hours}`;
  box.innerHTML =
    `<span class="rd">Накоплено снимков: <b>${hours}</b></span>` +
    `<span class="rd">${bit(r.activity, r.need_activity, "движение товара")}</span>` +
    `<span class="rd">${bit(r.levels, r.need_levels, "уровни цен")}</span>`;
}

function headRow() {
  return `<div class="row head">
    <span></span><span>Товар</span>
    <span class="num" title="Цена самого дешёвого лота — столько стоит купить одну штуку прямо сейчас">Цена сейчас</span>
    <span class="num" title="Цена, к которой вернётся прилавок, когда дешёвые лоты разберут">Цена после</span>
    <span class="num" title="Сколько золота нужно, чтобы скупить все лоты дешевле «цены после»">Скупить всё</span>
    <span class="num" title="Сколько останется чистыми, если перепродать скупленное по «цене после», за вычетом комиссии аукциона 5%">Навар</span>
    <span class="state">Совет</span>
  </div>`;
}

function row(it) {
  const rank = it.rank_of > 1 ? ` · ранг ${it.rank}` : "";
  // Метка «старьё» и измеренное движение — два предупреждения, которые
  // должны быть видны БЕЗ наведения: именно они отличают находку от
  // ловушки, а по спреду они неразличимы.
  const era = it.current_era
    ? ""
    : ` <span class="tag" title="Товар не из текущего дополнения: большой спред у такого обычно оттого, что рынок никто не смотрит">старьё</span>`;
  const move =
    it.activity_pct === null || it.activity_pct === undefined
      ? ""
      : ` · ${it.activity_pct < 1 ? "стоит" : "движение " + it.activity_pct + "%"}`;
  // Цветом отмечаем только то, где есть настоящие деньги. Порог тот же,
  // что в аналитике (MIN_UPSIDE_GOLD): два места, одно значение по смыслу.
  const deal = it.upside_gold >= 500 ? " is-deal" : "";
  // «unknown» намеренно пустое: пока истории по товару нет, приложение
  // молчит, а не выдаёт фазу патча за совет по конкретному товару.
  const words = { buy: "брать", sell: "продавать", hold: "держать", unknown: "копим" };
  const icon = it.icon
    ? `<img class="icon" src="${it.icon}" alt="">`
    : `<span class="icon"></span>`;
  return `<div class="row" title="${escapeHtml((it.reasons || []).join("\n"))}">
    ${icon}
    <span class="iname">${escapeHtml(it.name)}${rank}${era}
      <span class="isub">${escapeHtml(it.subclass || "")} · ${it.quantity.toLocaleString("ru-RU")} шт${move}</span>
    </span>
    <span class="num">${goldFine(it.floor_gold)}</span>
    <span class="num soft">${goldFine(it.market_gold)}</span>
    <span class="num soft">${it.deal_qty ? gold(it.deal_cost_gold) : "—"}</span>
    <span class="num gap${deal}">${it.upside_gold >= 1 ? "+" + gold(it.upside_gold) : "—"}</span>
    <span class="state" data-state="${it.state}">${words[it.state] || ""}</span>
  </div>`;
}

$("#a-refresh").addEventListener("click", async () => {
  $("#a-note").textContent = "Спрашиваю Blizzard…";
  state.auction = null;
  await window.pywebview.api.refresh_auction(state.region);
});

/* ---------- события из Python ---------- */

window.kmarket = {
  emit(event) {
    if (event.type === "live") {
      $("#v-price").innerHTML = gold(event.price) + ' <span class="unit">g</span>';
      $("#stamp").textContent = "живая цена получена";
    } else if (event.type === "report") {
      renderReport(event.data);
      state.ready = true;
    } else if (event.type === "auction-ready") {
      state.auction = null; // пересобрать при следующем открытии вкладки
      state.ready = true;
      if ($('.view[data-view="auction"]').classList.contains("is-on")) loadAuction();
    } else if (event.type === "note") {
      $("#a-note").textContent = event.text;
    } else if (event.type === "error") {
      showError(event.text);
    }
  },
};

function reload() {
  state.report = null;
  state.auction = null;
  state.ready = false;
  $("#stamp").textContent = "загрузка…";
  window.pywebview.api.start_load(state.region);
}

window.addEventListener("pywebviewready", async () => {
  const boot = await window.pywebview.api.bootstrap();
  state.region = boot.primary || "eu";
  markRegion();
  reload();
});
