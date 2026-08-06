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
  `;
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
  note.textContent =
    `Под слежкой ${data.tracked} товаров. Окно сравнения ${data.window_days} дн.` +
    (ev
      ? ` Ближайшее событие: ${ev.label}, ${
          ev.days > 0 ? "через " + ev.days + " дн" : Math.abs(ev.days) + " дн назад"
        }.`
      : "");

  const bargains = data.bargains || [];
  $("#a-bargains").innerHTML = bargains.length
    ? `<div class="label" style="padding:6px 0 8px">Недооценённые прямо сейчас</div>
       <div class="rows">${headRow()}${bargains.map(row).join("")}</div>
       <div style="height:12px"></div>`
    : "";

  $("#a-rows").innerHTML = headRow() + data.items.map(row).join("");
}

function headRow() {
  return `<div class="row head">
    <span></span><span>Товар</span>
    <span class="num">Пол</span><span class="num">Уровень</span>
    <span class="num">Вложить</span><span class="num">Вернуть</span>
    <span class="state">Совет</span>
  </div>`;
}

function row(it) {
  const rank = it.rank_of > 1 ? ` · ранг ${it.rank}` : "";
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
    <span class="iname">${escapeHtml(it.name)}${rank}
      <span class="isub">${escapeHtml(it.subclass || "")} · ${it.quantity.toLocaleString("ru-RU")} шт</span>
    </span>
    <span class="num">${goldFine(it.floor_gold)}</span>
    <span class="num soft">${goldFine(it.market_gold)}</span>
    <span class="num soft">${it.deal_qty ? gold(it.deal_cost_gold) : "—"}</span>
    <span class="num gap${deal}">${it.upside_gold >= 1 ? "+" + gold(it.upside_gold) : "—"}</span>
    <span class="state" data-state="${it.state}">${words[it.state] || ""}</span>
  </div>`;
}

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
