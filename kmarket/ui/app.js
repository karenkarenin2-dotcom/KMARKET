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
  let when = "";
  let ageMin = null;
  if (data.live_utc) {
    ageMin = Math.round((Date.now() - new Date(data.live_utc)) / 60000);
    when =
      "Снимок Blizzard от " +
      new Date(data.live_utc).toLocaleTimeString("ru-RU", {
        hour: "2-digit", minute: "2-digit",
      }) +
      ` (${ageMin} мин назад).`;
  } else if (data.stale) {
    when = "Живой снимок не получен, показываю последний сохранённый.";
  }
  note.textContent =
    `${when} Под слежкой ${data.tracked} товаров.` +
    (ev
      ? ` Ближайшее событие: ${ev.label}, ${
          ev.days > 0 ? "через " + ev.days + " дн" : Math.abs(ev.days) + " дн назад"
        }.`
      : "");

  renderAccuracy(ageMin);
  renderReadiness(data.readiness);
  renderRhythm(data.rhythm);

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
/* Насколько можно верить числам в таблице.
 *
 * Измерено на 32 снимках: между соседними часами цена пола гуляет на 0.3%,
 * цена после — на 0.0%, общий запас на 2.5%, а вот ДЕШЁВЫЙ ХВОСТ на 19.8%,
 * и у каждого четвёртого товара больше чем на 50%. Снимок у Blizzard
 * обновляется раз в час, обойти это нельзя.
 *
 * Значит колонки делятся на надёжные (цены) и оценочные (количество,
 * вложение, навар). Молчать об этом — значит выдавать оценку за факт:
 * Карен сравнил наши 5 187 з с настоящими 39 804 з в игре и справедливо
 * решил, что софт врёт. Софт не врал, но и не предупредил. */
function renderAccuracy(ageMin) {
  const box = $("#a-accuracy");
  if (ageMin === null || ageMin === undefined) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const stale = ageMin > 40;
  box.innerHTML =
    `<b>Цены точные, количество — оценка.</b> Пол и «цена после» между ` +
    `снимками почти не меняются (0.3% и 0.0%), а дешёвый хвост — на 20%, ` +
    `у каждого четвёртого товара больше чем вдвое. Поэтому «скупить всё» и ` +
    `«навар» смотри как порядок величины и сверяйся с прилавком в игре: ` +
    `там может оказаться и меньше, и заметно больше.` +
    (stale
      ? ` <span class="warn">Снимку уже ${ageMin} мин — скоро выйдет новый.</span>`
      : "");
}

/* Недельный ритм. Показывается, только когда истории хватает: рисовать
 * «лучший час недели» по трём дням значит выдавать шум за расписание. */
function renderRhythm(r) {
  const card = $("#a-rhythm");
  if (!r) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const body = $("#a-rhythm-body");

  if (!r.enough) {
    body.innerHTML =
      `<div class="note">Накоплено ${r.days} сут из ${r.need_days} — ` +
      `недельный рисунок пока не считаем, чтобы не выдать шум за расписание.</div>`;
    return;
  }

  const cell = (c) => `${c.weekday} ${String(c.hour).padStart(2, "0")}:00`;
  const buy = r.cheapest[0];
  const sell = r.dearest[0];
  body.innerHTML =
    `<div class="rhythm">
       <div class="rh">
         <span class="rh-when">${cell(buy)}</span>
         <span class="rh-what">дешевле всего · ${buy.deviation.toFixed(1)}%</span>
       </div>
       <div class="rh">
         <span class="rh-when">${cell(sell)}</span>
         <span class="rh-what">дороже всего · +${sell.deviation.toFixed(1)}%</span>
       </div>
       <div class="rh">
         <span class="rh-when">${r.spread_pct.toFixed(1)}%</span>
         <span class="rh-what">разброс внутри недели</span>
       </div>
     </div>
     <div class="note">Брать в дешёвый час, продавать запасённое в дорогой.
       ${r.reliable ? "" : "Истории пока " + r.days + " сут — рисунок ещё уточнится."}</div>`;
}

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
    `<span class="rd">${
      r.sold
        ? `<b>скорость продаж — работает</b> (${r.sold} из ${r.total} товаров)`
        : `скорость продаж — нужно ${r.need_sold} измеренных переходов`
    }</span>` +
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
  // Измеренный срок распродажи бьёт косвенное «движение»: он прямо
  // отвечает на вопрос «когда я верну деньги».
  let move = "";
  if (it.hours_to_clear !== null && it.hours_to_clear !== undefined) {
    const h = it.hours_to_clear;
    const t = h < 48 ? Math.round(h) + " ч" : (h / 24).toFixed(1) + " сут";
    // Очередь важнее срока: именно она объясняет, почему срок такой.
    const q = it.wall_qty
      ? ` (в очереди ${it.wall_qty.toLocaleString("ru-RU")} шт)`
      : "";
    move = ` · продашь через ~${t}${q}`;
  } else if (it.sold_per_hour !== null && it.sold_per_hour !== undefined) {
    move = ` · уходит ${it.sold_per_hour} шт/ч`;
  } else if (it.activity_pct !== null && it.activity_pct !== undefined) {
    move = ` · ${it.activity_pct < 1 ? "стоит" : "движение " + it.activity_pct + "%"}`;
  }
  // Постоянство разрыва: главный ответ на «идти ли туда». Точное
  // количество мы всё равно не знаем (снимок часовой), а вот бывает ли
  // тут скидка вообще — знаем хорошо.
  if (it.gap_rate !== null && it.gap_rate !== undefined) {
    move +=
      it.gap_rate >= 50
        ? ` · <span class="perm">скидка в ${it.gap_rate}% снимков</span>`
        : ` · разовая (${it.gap_rate}%)`;
  }
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
    <span class="num soft">${
      it.deal_qty
        ? gold(it.deal_cost_gold) +
          `<span class="sub">${it.deal_qty.toLocaleString("ru-RU")} шт</span>`
        : "—"
    }</span>
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
