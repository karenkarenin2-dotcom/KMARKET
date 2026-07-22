/* KMARKET — дашборд. Никаких библиотек: график рисуется руками на canvas,
   тепловая карта — обычной CSS-сеткой. Так страница остаётся
   самодостаточной и не тянет ничего из интернета. */

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

const state = { region: 'eu', days: 90, report: null, chart: null };

/* ---------- форматирование ---------- */

const gold = (n) => Math.round(n).toLocaleString('ru-RU').replace(/ /g, ' ');
const pct = (n, digits = 0) => `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`;

function when(iso) {
  const d = new Date(iso);
  return d.toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
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
  const app = document.getElementById('app');
  app.innerHTML = '<p class="loading">Считаю историю и бэктест — несколько секунд…</p>';
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

  /* вердикт */
  slot('verdict').dataset.state = report.verdict.state;
  slot('word').textContent = report.verdict.title;
  slot('summary').textContent = report.verdict.summary;
  slot('price').textContent = `${gold(report.current.price)} g`;
  slot('priceMeta').textContent =
    `${when(report.current.updated_local)} · ${ageText(report.current.age_minutes)}`;

  /* причины */
  slot('reasons').innerHTML = report.verdict.reasons.map((r) => `<li>${r}</li>`).join('');
  slot('confidence').textContent = `Уверенность: ${report.verdict.confidence}`;

  /* переключатель периода */
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

  /* окна */
  slot('windows').innerHTML = report.windows.map((w) => `
    <tr${w.days === 90 ? ' class="highlight"' : ''}>
      <td>${w.label}</td><td>${gold(w.low)}</td><td>${gold(w.median)}</td>
      <td>${gold(w.high)}</td><td>${w.spread_pct.toFixed(0)}%</td>
      <td>${w.percentile.toFixed(0)}</td>
    </tr>`).join('');

  /* сезонность */
  renderSeasonality(report.seasonality, slot);

  /* бэктест */
  renderBacktest(report.backtest, slot);

  /* подвал */
  const coverage = report.history.coverage;
  slot('footer').innerHTML =
    `История: ${gold(report.history.points)} точек с ${day(report.history.since)} · ` +
    `за последние 30 дней шаг ${coverage.median_gap_minutes} мин, худший разрыв ${coverage.max_gap_hours} ч · ` +
    `архив до сегодня — data.wowtoken.app, дальше собственный сбор · ` +
    `пересчитано ${when(report.generated_at)}`;

  app.append(view);
  drawChart();
}

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
  season.matrix.forEach((row, day) => {
    cells.push(`<div class="h-label">${WEEKDAYS[day]}</div>`);
    row.forEach((value, hour) => {
      if (value === null) {
        cells.push('<div class="cell"></div>');
        return;
      }
      const title = `${WEEKDAYS[day]} ${String(hour).padStart(2, '0')}:00 — ${pct(value, 2)} к своему уровню`;
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
  const height = 320;
  const width = canvas.clientWidth || canvas.parentElement.clientWidth;
  const dpr = window.devicePixelRatio || 1;

  canvas.style.height = `${height}px`;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  if (points.length < 2) return;

  const pad = { left: 66, right: 8, top: 14, bottom: 26 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const times = points.map((p) => new Date(p[0]).getTime());
  const prices = points.map((p) => p[1]);
  const tMin = times[0], tMax = times[times.length - 1];
  let pMin = Math.min(...prices), pMax = Math.max(...prices);
  const margin = (pMax - pMin) * 0.08 || 1;
  pMin -= margin; pMax += margin;

  const x = (t) => pad.left + ((t - tMin) / (tMax - tMin || 1)) * plotW;
  const y = (p) => pad.top + (1 - (p - pMin) / (pMax - pMin || 1)) * plotH;

  /* сетка и подписи цен */
  ctx.font = '11px ui-monospace, Consolas, monospace';
  ctx.fillStyle = '#6b7280';
  ctx.strokeStyle = '#262931';
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

  /* заливка под линией */
  const fill = ctx.createLinearGradient(0, pad.top, 0, pad.top + plotH);
  fill.addColorStop(0, 'rgba(230,232,236,.10)');
  fill.addColorStop(1, 'rgba(230,232,236,0)');
  ctx.beginPath();
  ctx.moveTo(x(times[0]), pad.top + plotH);
  points.forEach((p, i) => ctx.lineTo(x(times[i]), y(prices[i])));
  ctx.lineTo(x(times[times.length - 1]), pad.top + plotH);
  ctx.closePath();
  ctx.fillStyle = fill;
  ctx.fill();

  /* линия */
  ctx.beginPath();
  points.forEach((p, i) => (i ? ctx.lineTo(x(times[i]), y(prices[i])) : ctx.moveTo(x(times[i]), y(prices[i]))));
  ctx.strokeStyle = '#e6e8ec';
  ctx.lineWidth = 1.6;
  ctx.lineJoin = 'round';
  ctx.stroke();

  /* текущая точка — золотом: единственный акцент на графике */
  const lastX = x(times[times.length - 1]), lastY = y(prices[prices.length - 1]);
  ctx.beginPath();
  ctx.arc(lastX, lastY, 3.5, 0, Math.PI * 2);
  ctx.fillStyle = '#e8c84a';
  ctx.fill();

  attachHover(canvas, { points, times, prices, x, y, pad, width, height, plotH });
}

function attachHover(canvas, ctxData) {
  const { times, prices, x, y, pad, width, height } = ctxData;
  canvas.onmousemove = (event) => {
    const rect = canvas.getBoundingClientRect();
    const mouseX = event.clientX - rect.left;
    let nearest = 0, best = Infinity;
    for (let i = 0; i < times.length; i++) {
      const distance = Math.abs(x(times[i]) - mouseX);
      if (distance < best) { best = distance; nearest = i; }
    }
    canvas.title = `${when(new Date(times[nearest]).toISOString())} — ${gold(prices[nearest])} g`;
  };
}

window.addEventListener('resize', () => drawChart());
renderRegions();
load();
