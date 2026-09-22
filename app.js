const state = { data: null, selectedDay: null };
const $ = (selector) => document.querySelector(selector);

const fmtPrice = (value) => Number(value).toFixed(4);
const chicagoDate = (iso) => new Intl.DateTimeFormat("en-CA", { timeZone: "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(iso));
const timeLabel = (iso) => new Intl.DateTimeFormat("en-US", { timeZone: "America/Chicago", hour: "numeric", minute: "2-digit" }).format(new Date(iso));
const dayLabel = (date) => new Intl.DateTimeFormat("en-US", { timeZone: "America/Chicago", weekday: "short", month: "short", day: "numeric" }).format(new Date(`${date}T12:00:00-05:00`));

function renderBrief(data) {
  const { bias, lastWeek, quote, priorDay } = data;
  const directionClass = bias.direction.toLowerCase();
  $("#bias-word").textContent = bias.direction;
  $("#bias-word").className = directionClass;
  $("#confidence-chip").textContent = `${bias.confidence} confidence`;
  $("#bias-summary").textContent = `${bias.direction} is the starting hypothesis while price trades ${quote.price >= lastWeek.midpoint ? "above" : "below"} last week’s midpoint. The market-maker version: let price raid the wrong side first, then demand confirmation toward ${bias.draw.toLowerCase()}.`;
  $("#week-low").textContent = fmtPrice(lastWeek.low);
  $("#week-high").textContent = fmtPrice(lastWeek.high);
  $("#midpoint-label").textContent = `Midpoint ${fmtPrice(lastWeek.midpoint)}`;
  const percent = Math.max(0, Math.min(100, ((quote.price - lastWeek.low) / (lastWeek.high - lastWeek.low)) * 100));
  $("#price-marker").style.left = `${percent}%`;
  $("#price-marker").title = `Current price ${fmtPrice(quote.price)}`;
  $("#draw-label").textContent = bias.draw;
  $("#draw-price").textContent = fmtPrice(bias.drawPrice);
  $("#confirmation-label").textContent = bias.confirmation;
  $("#invalidation-label").textContent = fmtPrice(bias.invalidation);
  $("#score-value").textContent = bias.score > 0 ? `+${bias.score}` : bias.score;
  $("#news-risk-label").textContent = `${bias.newsRisk} risk`;
  $("#risk-count").textContent = `${bias.todayEventCount} event${bias.todayEventCount === 1 ? "" : "s"}`;
  $("#current-price").textContent = fmtPrice(quote.price);
  $("#updated-label").textContent = `Updated ${timeLabel(data.meta.updatedAt)} CT`;

  $("#evidence-list").innerHTML = bias.evidence.map(item => `
    <div class="evidence-item ${item.tone}">
      <span class="evidence-bar" aria-hidden="true"></span>
      <div><strong>${item.label}</strong><span>${item.value}</span></div>
    </div>`).join("") + `
    <div class="evidence-item">
      <span class="evidence-bar" aria-hidden="true"></span>
      <div><strong>Prior-day range</strong><span>${fmtPrice(priorDay.l)} – ${fmtPrice(priorDay.h)}</span></div>
    </div>`;
}

function renderCalendar(data) {
  const dated = data.calendar.filter(event => event.timeChicago);
  const dates = [...new Set(dated.map(event => chicagoDate(event.timeChicago)))].sort();
  const today = chicagoDate(data.meta.updatedAt);
  state.selectedDay = dates.includes(today) ? today : dates[0];
  const tabs = $("#day-tabs");
  tabs.innerHTML = dates.map(date => `<button class="day-tab ${date === state.selectedDay ? "active" : ""}" role="tab" aria-selected="${date === state.selectedDay}" data-day="${date}">${dayLabel(date)}</button>`).join("");
  tabs.addEventListener("click", event => {
    const button = event.target.closest("button[data-day]");
    if (!button) return;
    state.selectedDay = button.dataset.day;
    tabs.querySelectorAll("button").forEach(item => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", String(active));
    });
    renderEvents();
  });
  renderEvents();
}

function renderEvents() {
  const events = state.data.calendar.filter(event => event.timeChicago && chicagoDate(event.timeChicago) === state.selectedDay);
  const target = $("#event-list");
  if (!events.length) {
    target.innerHTML = `<div class="empty-state">No scheduled GBP or USD events for this day.</div>`;
    return;
  }
  target.innerHTML = events.map(event => `
    <article class="event-row">
      <time class="event-time" datetime="${event.timeChicago}">${timeLabel(event.timeChicago)}</time>
      <span class="currency">${event.currency}</span>
      <span class="impact ${event.impact.toLowerCase()}">${event.impact}</span>
      <a class="event-title" href="${event.url}" target="_blank" rel="noreferrer">${event.title}</a>
      <span class="event-stat">Forecast <strong>${event.forecast || "—"}</strong></span>
      <span class="event-stat">Previous <strong>${event.previous || "—"}</strong></span>
    </article>`).join("");
}

function drawChart(data) {
  const canvas = $("#price-chart");
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const candles = data.candles.slice(-180);
  if (!candles.length) { $("#chart-empty").hidden = false; return; }
  const levels = [data.lastWeek.high, data.lastWeek.low, data.lastWeek.midpoint];
  const min = Math.min(...candles.map(c => c.l), ...levels);
  const max = Math.max(...candles.map(c => c.h), ...levels);
  const pad = { top: 18, right: 70, bottom: 28, left: 4 };
  const width = rect.width - pad.left - pad.right;
  const height = rect.height - pad.top - pad.bottom;
  const y = value => pad.top + ((max - value) / (max - min || 1)) * height;
  const step = width / candles.length;
  const body = Math.max(1, Math.min(5, step * .58));

  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.font = "10px ui-monospace, monospace";
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const value = min + ((max - min) * i / 4);
    const py = y(value);
    ctx.strokeStyle = "rgba(141,153,167,.12)";
    ctx.beginPath(); ctx.moveTo(0, py); ctx.lineTo(rect.width - pad.right + 8, py); ctx.stroke();
    ctx.fillStyle = "#778391";
    ctx.fillText(fmtPrice(value), rect.width - 3, py + 3);
  }

  const special = [
    { value:data.lastWeek.high, color:"#6fe3b1", label:"PWH" },
    { value:data.lastWeek.midpoint, color:"#ffcb66", label:"50%" },
    { value:data.lastWeek.low, color:"#ff7777", label:"PWL" }
  ];
  special.forEach(level => {
    const py = y(level.value);
    ctx.setLineDash([4, 5]); ctx.strokeStyle = level.color; ctx.globalAlpha = .6;
    ctx.beginPath(); ctx.moveTo(0, py); ctx.lineTo(rect.width - pad.right + 8, py); ctx.stroke();
    ctx.globalAlpha = 1; ctx.setLineDash([]); ctx.fillStyle = level.color; ctx.fillText(level.label, rect.width - pad.right - 2, py - 4);
  });

  candles.forEach((candle, index) => {
    const x = pad.left + index * step + step / 2;
    const rising = candle.c >= candle.o;
    const color = rising ? "#6fe3b1" : "#ff7777";
    ctx.strokeStyle = color; ctx.fillStyle = color;
    ctx.beginPath(); ctx.moveTo(x, y(candle.h)); ctx.lineTo(x, y(candle.l)); ctx.stroke();
    const top = y(Math.max(candle.o, candle.c));
    const bottom = y(Math.min(candle.o, candle.c));
    ctx.fillRect(x - body / 2, top, body, Math.max(1, bottom - top));
  });

  ctx.textAlign = "left"; ctx.fillStyle = "#778391";
  const first = new Date(candles[0].t * 1000);
  const last = new Date(candles[candles.length - 1].t * 1000);
  ctx.fillText(new Intl.DateTimeFormat("en-US", { weekday:"short", hour:"numeric", timeZone:"America/Chicago" }).format(first), 2, rect.height - 5);
  ctx.textAlign = "right";
  ctx.fillText(new Intl.DateTimeFormat("en-US", { weekday:"short", hour:"numeric", minute:"2-digit", timeZone:"America/Chicago" }).format(last), rect.width - pad.right, rect.height - 5);
}

async function init() {
  try {
    const response = await fetch("data/market.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`Data request failed: ${response.status}`);
    state.data = await response.json();
    renderBrief(state.data);
    renderCalendar(state.data);
    drawChart(state.data);
    window.addEventListener("resize", () => drawChart(state.data));
  } catch (error) {
    console.error(error);
    $("#updated-label").textContent = "Brief unavailable";
    $("#chart-empty").hidden = false;
  }
}

init();
