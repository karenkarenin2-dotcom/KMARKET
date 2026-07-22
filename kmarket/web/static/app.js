/* KMARKET — дашборд. Никаких библиотек: график рисуется руками на canvas,
   тепловая карта — CSS-сеткой, спарклайны — инлайновым SVG. Страница
   самодостаточна и ничего не тянет из интернета. */

const REGIONS = [
  { code: 'eu', title: 'EU' },
  { code: 'us', title: 'US' },
];
const RANGES = [
  { days: 7, title: '7 дней' },
  { days: 30, title: '30 дней' },
  { days: 90, title: '90 дней' },
  { days: 365, title: 'год' },
  { days: 100000, title: 'всё' },
];
const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

const COLOR = {
  ink: '#e6e8ec',
  ink3: '#6b7280',
  line: '#262931',
  gold: '#e8c84a',
  bg2: '#1b1d22',
};

const state = { region: 'eu', days: 90, report: null, chart: null };
let geom = null; // геометрия последнего отрисованного графика

/* ---------- форматирование ---------- */

const gold = (n) => Math.round(n).toLocaleString('ru-RU').replace(/ /g, ' ');
const pct = (n, digits = 0) => `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`;

function when(iso) {
  return new Date(iso).toLocaleString('ru-RU',
    { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

/* Для дат вроде начала истории год обязателен: «с 19 нояб.» без года
   выглядит как позапрошлая неделя, а речь про 2020-й. */
function day(iso) {
  return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' });
}

function ageText(minutes) {
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} ч назад`;
  return `${Math.round(hours / 24)} дн назад`;
}

/* ---------- загрузка ---------- */

async function load() {
  document.getElementById('app').innerHTML =
    '<p class="loading">Считаю историю, события и бэктест — несколько секунд…</p>';
  const [report, chart] = await Promise.all([
    fetch(`/api/report/${state.region}`).then((r) => r.json()),
    fetch(`/api/chart/${state.region}?days=${state.days}`).then((r) => r.json()),
  ]);
  state.report = report;
  state.chart = chart;
  render();
}

async function reloadChart() {
  state.chart = await fetch(`/api/chart/${state.region}?days=${state.days}`).then((r) => r.json());
  drawChart();
  renderChartEvents();
}

/* ---------- отрисовка ---------- */

function renderRegions() {
  const box = document.getElementById('regions');
  box.innerHTML = '';
  for (const region of REGIONS) {
    const button = document.createElement('button');
    button.textContent = region.title;
    button.setAttribute('aria-pressed', String(region.code === state.region));
    button.onclick = () => { state.region = region.code; renderRegions(); load(); };
    box.append(button);
  }
}

function render() {
  const report = state.report;
  const app = document.getElementById('app');
  app.innerHTML = '';

  if (report.empty) {
    app.innerHTML = '<p class="loading">Истории пока нет. Запусти <code>python -m kmarket.collect</code>.</p>';
    return;
  }

  const view = document.getElementById('tpl-report').content.cloneNode(true);
  const slot = (name) => view.querySelector(`[data-slot="${name}"]`);

  slot('verdict').dataset.state = report.verdict.state;
  slot('word').textContent = report.verdict.title;
  slot('summary').textContent = report.verdict.summary;
  slot('price').textContent = `${gold(report.current.price)} g`;
  slot('priceMeta').textContent =
    `${when(report.current.updated_local)} · ${ageText(report.current.age_minutes)}`;

  slot('reasons').innerHTML = report.verdict.reasons.map((r) => `<li>${r}</li>`).join('');
  slot('confidence').textContent = `Уверенность: ${report.verdict.confidence}`;

  const ranges = slot('ranges');
  for (const range of RANGES) {
    const button = document.createElement('button');
    button.textContent = range.title;
    button.setAttribute('aria-pressed', String(range.days === state.days));
    button.onclick = () => {
      state.days = range.days;
      ranges.querySelectorAll('button').forEach((b, i) =>
        b.setAttribute('aria-pressed', String(RANGES[i].days === state.days)));
      reloadChart();
    };
    ranges.append(button);
  }
  slot('chartNote').textContent =
    `Время местное (${report.timezones.local}). Тренд: ${report.trend.direction}` +
    (report.trend.change_24h_pct != null ? `, за сутки ${pct(report.trend.change_24h_pct, 1)}` : '') +
    (report.trend.change_7d_pct != null ? `, за неделю ${pct(report.trend.change_7d_pct, 1)}` : '');

  slot('windows').innerHTML = report.windows.map((w) => `
    <tr${w.days === 90 ? ' class="highlight"' : ''}>
      <td>${w.label}</td><td>${gold(w.low)}</td><td>${gold(w.median)}</td>
      <td>${gold(w.high)}</td><td>${w.spread_pct.toFixed(0)}%</td>
      <td>${w.percentile.toFixed(0)}</td>
    </tr>`).join('');

  renderSeasonality(report.seasonality, slot);
  renderEventStudies(report.events, slot);
  renderBacktest(report.backtest, slot);

  const coverage = report.history.coverage;
  slot('footer').innerHTML =
    `История: ${gold(report.history.points)} точек с ${day(report.history.since)} · ` +
    `за последние 30 дней шаг ${coverage.median_gap_minutes} мин, худший разрыв ${coverage.max_gap_hours} ч · ` +
    `архив до сегодня — data.wowtoken.app, дальше собственный сбор · ` +
    `пересчитано ${when(report.generated_at)}`;

  app.append(view);
  drawChart();
  renderChartEvents();
  watchChart();
}

/* ---------- сезонность ---------- */

function renderSeasonality(season, slot) {
  const box = slot('heatmap');
  if (!season) {
    box.innerHTML = '<p class="note">Данных пока не хватает.</p>';
    return;
  }
  slot('seasonNote').textContent =
    `Время серверов (${season.timezone}), ${gold(season.points)} точек за ${season.days} дней` +
    (season.reliable ? '' : ' — данных мало, читать как намёк');

  const values = season.matrix.flat().filter((v) => v !== null).map(Math.abs).sort((a, b) => a - b);
  const scale = values.length ? values[Math.floor(values.length * 0.9)] || 1 : 1;

  const cells = ['<div class="h-label"></div>'];
  for (let hour = 0; hour < 24; hour++) {
    cells.push(`<div class="h-label h-hour">${String(hour).padStart(2, '0')}</div>`);
  }
  season.matrix.forEach((row, weekday) => {
    cells.push(`<div class="h-label">${WEEKDAYS[weekday]}</div>`);
    row.forEach((value, hour) => {
      if (value === null) { cells.push('<div class="cell"></div>'); return; }
      const title = `${WEEKDAYS[weekday]} ${String(hour).padStart(2, '0')}:00 — ${pct(value, 2)} к своему уровню`;
      cells.push(`<div class="cell" style="background:${heatColor(value, scale)}" title="${title}"></div>`);
    });
  });
  box.innerHTML = cells.join('');

  const line = (c) => `<li><b>${c.weekday} ${String(c.hour).padStart(2, '0')}:00</b> — ${pct(c.deviation, 2)}</li>`;
  slot('bestCells').innerHTML = season.best.map(line).join('');
  slot('worstCells').innerHTML = season.worst.map(line).join('');
}

function heatColor(value, scale) {
  const t = Math.max(-1, Math.min(1, value / scale));
  const neutral = [58, 63, 73];
  const target = t < 0 ? [66, 147, 129] : [198, 108, 98];
  const k = Math.abs(t);
  return `rgb(${neutral.map((c, i) => Math.round(c + (target[i] - c) * k)).join(',')})`;
}

/* ---------- события ---------- */

function renderEventStudies(events, slot) {
  if (!events || !events.studies.length) {
    slot('eventStudies').innerHTML = '<p class="note">Данных пока не хватает.</p>';
    return;
  }
  const now = events.now;
  slot('eventNote').textContent = now
    ? `Сейчас: ${now.label}, ${now.days > 0 ? `через ${now.days} дн` : `${Math.abs(now.days)} дн назад`}`
    : 'Ближайших событий в пределах 30 дней нет';

  const arrow = (v) => (v == null ? '—' : `<span class="${v > 0 ? 'up' : 'down'}">${pct(v, 1)}</span>`);
  slot('eventStudies').innerHTML = events.studies.map((s) => `
    <div class="event-card">
      <h3>${s.title}</h3>
      <div class="count">${s.events} событий в истории</div>
      ${sparkline(s.curve)}
      <div class="phases">
        <div><span>${arrow(s.before_pct)}</span><span class="cap">за 30–8 дн до</span></div>
        <div><span>${arrow(s.around_pct)}</span><span class="cap">±7 дней</span></div>
        <div><span>${arrow(s.after_pct)}</span><span class="cap">8–30 дн после</span></div>
      </div>
    </div>`).join('');
}

/* Спарклайн кривой «что с ценой вокруг события»: золотой пунктир — сам
   день события, серая линия — нулевой уровень окна. */
function sparkline(curve) {
  if (!curve || curve.length < 2) return '';
  const w = 240, h = 64;
  const xs = curve.map((p) => p[0]), ys = curve.map((p) => p[1]);
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const yMin = Math.min(...ys), yMax = Math.max(...ys);
  const pad = (yMax - yMin) * 0.15 || 1;
  const lo = yMin - pad, hi = yMax + pad;
  const X = (o) => ((o - xMin) / (xMax - xMin || 1)) * w;
  const Y = (v) => h - ((v - lo) / (hi - lo || 1)) * h;
  const path = curve.map((p) => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(' ');
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <line x1="0" y1="${Y(0).toFixed(1)}" x2="${w}" y2="${Y(0).toFixed(1)}"
          stroke="${COLOR.line}" stroke-width="1" vector-effect="non-scaling-stroke"/>
    <line x1="${X(0).toFixed(1)}" y1="0" x2="${X(0).toFixed(1)}" y2="${h}"
          stroke="${COLOR.gold}" stroke-width="1" stroke-dasharray="3 3" opacity=".65"
          vector-effect="non-scaling-stroke"/>
    <polyline points="${path}" fill="none" stroke="${COLOR.ink}" stroke-width="1.6"
          stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
  </svg>`;
}

function renderChartEvents() {
  const slot = document.querySelector('[data-slot="chartEvents"]');
  if (!slot) return;
  const marks = (state.chart && state.chart.events) || [];
  slot.innerHTML = marks.length
    ? `Отмечено на графике: ${marks.map((e) => `${e.label} (${day(e.date)})`).join(' · ')}`
    : '';
}

/* ---------- бэктест ---------- */

function renderBacktest(backtest, slot) {
  const stock = backtest.stockpile;
  const rule = backtest.rule;
  if (stock) {
    slot('stockpile').innerHTML = `
      <div>
        <div class="big">${pct(stock.saving_pct, 1)}</div>
        <div class="cap">экономия против покупки наугад</div>
      </div>
      <div>
        <div class="big">${gold(stock.saving_gold)} g</div>
        <div class="cap">на каждом жетоне</div>
      </div>
      <p>Правило вердикта — «брать, когда цена в нижних ${rule.threshold}% за
      ${rule.window_days} дней» — проверено на ${stock.years} полных годах истории.
      Сравнение идёт внутри каждого года, поэтому долгий рост цены на результат
      не влияет. Профиль: ${stock.per_year} жетонов в год с возможностью ждать.
      Средняя цена по правилу ${gold(stock.avg_strategy_price)} g против
      ${gold(stock.avg_even_price)} g при покупке первого числа каждого месяца.</p>`;
  }
  slot('grid').innerHTML = backtest.grid
    .filter((r) => r.local_advantage_pct !== null)
    .map((r) => `
      <tr${r.window_days === rule.window_days && r.threshold === rule.threshold ? ' class="highlight"' : ''}>
        <td>${r.window_days} дн</td><td>${r.threshold}</td>
        <td>${r.signals_per_year}</td><td>${pct(r.local_advantage_pct, 2)}</td>
        <td>${r.median_wait_days} дн</td>
      </tr>`).join('');
}

/* ---------- график ---------- */

function drawChart() {
  const canvas = document.getElementById('chart');
  if (!canvas || !state.chart) return;
  const points = state.chart.points;
  if (points.length < 2) { geom = null; return; }

  const height = 320;
  const width = canvas.clientWidth || canvas.parentElement.clientWidth || 0;
  // Нулевая ширина = раскладка ещё не случилась (свёрнутое окно, скрытая
  // вкладка, первый кадр). Рисовать нечего, но и сдаваться нельзя: сюда
  // вернёт ResizeObserver, как только канвас получит реальный размер.
  if (width < 2) { geom = null; return; }
  const dpr = window.devicePixelRatio || 1;
  canvas.style.height = `${height}px`;
  canvas.width = width * dpr;
  canvas.height = height * dpr;

  const pad = { left: 66, right: 8, top: 14, bottom: 26 };
  const times = points.map((p) => new Date(p[0]).getTime());
  const prices = points.map((p) => p[1]);
  const tMin = times[0], tMax = times[times.length - 1];
  let pMin = Math.min(...prices), pMax = Math.max(...prices);
  const margin = (pMax - pMin) * 0.08 || 1;
  pMin -= margin; pMax += margin;

  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  geom = {
    canvas, dpr, width, height, pad, times, prices, plotW, plotH,
    x: (t) => pad.left + ((t - tMin) / (tMax - tMin || 1)) * plotW,
    y: (p) => pad.top + (1 - (p - pMin) / (pMax - pMin || 1)) * plotH,
    tMin, tMax, pMin, pMax,
  };

  paint(null);
  attachHover(canvas);
}

function paint(hover) {
  if (!geom) return;
  const { canvas, dpr, width, height, pad, times, prices, x, y, plotH, tMin, tMax, pMin, pMax } = geom;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  /* сетка и подписи цен */
  ctx.font = '11px ui-monospace, Consolas, monospace';
  ctx.fillStyle = COLOR.ink3;
  ctx.strokeStyle = COLOR.line;
  ctx.lineWidth = 1;
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';
  for (let i = 0; i <= 4; i++) {
    const price = pMin + ((pMax - pMin) * i) / 4;
    const yy = Math.round(y(price)) + 0.5;
    ctx.beginPath(); ctx.moveTo(pad.left, yy); ctx.lineTo(width - pad.right, yy); ctx.stroke();
    ctx.fillText(gold(price), pad.left - 10, yy);
  }

  /* подписи дат */
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  for (let i = 0; i <= 4; i++) {
    const t = tMin + ((tMax - tMin) * i) / 4;
    const label = new Date(t).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
    ctx.fillText(label, Math.min(Math.max(x(t), pad.left + 20), width - pad.right - 20), height - pad.bottom + 8);
  }

  /* отметки игровых событий — до линии, чтобы не перекрывать её */
  for (const event of (state.chart.events || [])) {
    const t = new Date(event.date).getTime();
    if (t < tMin || t > tMax) continue;
    const xx = Math.round(x(t)) + 0.5;
    ctx.save();
    ctx.setLineDash([3, 4]);
    ctx.strokeStyle = event.kind === 'launch' ? 'rgba(232,200,74,.55)' : 'rgba(154,161,173,.32)';
    ctx.beginPath(); ctx.moveTo(xx, pad.top); ctx.lineTo(xx, pad.top + plotH); ctx.stroke();
    ctx.restore();
    ctx.beginPath();
    ctx.moveTo(xx, pad.top - 4); ctx.lineTo(xx - 3.5, pad.top - 9); ctx.lineTo(xx + 3.5, pad.top - 9);
    ctx.closePath();
    ctx.fillStyle = event.kind === 'launch' ? COLOR.gold : COLOR.ink3;
    ctx.fill();
  }

  /* заливка под линией */
  const fill = ctx.createLinearGradient(0, pad.top, 0, pad.top + plotH);
  fill.addColorStop(0, 'rgba(230,232,236,.10)');
  fill.addColorStop(1, 'rgba(230,232,236,0)');
  ctx.beginPath();
  ctx.moveTo(x(times[0]), pad.top + plotH);
  times.forEach((t, i) => ctx.lineTo(x(t), y(prices[i])));
  ctx.lineTo(x(times[times.length - 1]), pad.top + plotH);
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();

  /* линия */
  ctx.beginPath();
  times.forEach((t, i) => (i ? ctx.lineTo(x(t), y(prices[i])) : ctx.moveTo(x(t), y(prices[i]))));
  ctx.strokeStyle = COLOR.ink;
  ctx.lineWidth = 1.6;
  ctx.lineJoin = 'round';
  ctx.stroke();

  /* текущая точка — золотом */
  const last = times.length - 1;
  ctx.beginPath();
  ctx.arc(x(times[last]), y(prices[last]), 3.5, 0, Math.PI * 2);
  ctx.fillStyle = COLOR.gold;
  ctx.fill();

  if (hover != null) paintHover(ctx, hover);
}

/* Кружок под курсором: видно, на какой именно точке пика стоишь. */
function paintHover(ctx, index) {
  const { pad, times, prices, x, y, plotH, width } = geom;
  const hx = x(times[index]);
  const hy = y(prices[index]);

  ctx.save();
  ctx.setLineDash([2, 3]);
  ctx.strokeStyle = 'rgba(154,161,173,.45)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(Math.round(hx) + 0.5, pad.top);
  ctx.lineTo(Math.round(hx) + 0.5, pad.top + plotH);
  ctx.stroke();
  ctx.restore();

  /* кольцо, чтобы точка читалась поверх линии любой яркости */
  ctx.beginPath();
  ctx.arc(hx, hy, 5, 0, Math.PI * 2);
  ctx.fillStyle = COLOR.bg2;
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = COLOR.gold;
  ctx.stroke();

  /* подпись */
  const priceText = `${gold(prices[index])} g`;
  const dateText = when(new Date(times[index]).toISOString());
  ctx.font = '11px ui-monospace, Consolas, monospace';
  const boxW = Math.max(ctx.measureText(priceText).width, ctx.measureText(dateText).width) + 18;
  const boxH = 38;
  let boxX = hx + 12;
  if (boxX + boxW > width - 4) boxX = hx - 12 - boxW;
  const boxY = Math.min(Math.max(hy - boxH / 2, pad.top), pad.top + plotH - boxH);

  ctx.fillStyle = 'rgba(27,29,34,.96)';
  ctx.strokeStyle = COLOR.line;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.roundRect(boxX, boxY, boxW, boxH, 4);
  ctx.fill();
  ctx.stroke();

  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  ctx.fillStyle = COLOR.gold;
  ctx.fillText(priceText, boxX + 9, boxY + 8);
  ctx.fillStyle = COLOR.ink3;
  ctx.fillText(dateText, boxX + 9, boxY + 22);
}

function attachHover(canvas) {
  canvas.onmousemove = (event) => {
    if (!geom) return;
    const rect = canvas.getBoundingClientRect();
    const mouseX = event.clientX - rect.left;
    let nearest = 0, best = Infinity;
    for (let i = 0; i < geom.times.length; i++) {
      const distance = Math.abs(geom.x(geom.times[i]) - mouseX);
      if (distance < best) { best = distance; nearest = i; }
    }
    paint(nearest);
  };
  canvas.onmouseleave = () => paint(null);
}

/* ResizeObserver надёжнее window.resize: он ловит и появление канваса с
   нулевой шириной (раскладка ещё не случилась), и изменение ширины из-за
   соседних элементов, а не только изменение размера окна. */
let chartObserver = null;
let pendingFrame = 0;

function watchChart() {
  const canvas = document.getElementById('chart');
  if (!canvas || !window.ResizeObserver) return;
  if (chartObserver) chartObserver.disconnect();
  chartObserver = new ResizeObserver(() => {
    cancelAnimationFrame(pendingFrame);
    pendingFrame = requestAnimationFrame(() => {
      const width = canvas.clientWidth;
      // Перерисовываем только если ширина реально изменилась или графика ещё нет.
      if (width > 1 && (!geom || Math.abs(geom.width - width) > 1)) drawChart();
    });
  });
  chartObserver.observe(canvas);
}

window.addEventListener('resize', () => drawChart());
renderRegions();
load();
