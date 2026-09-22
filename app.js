const state = { data: null, selectedDay: null, chartMode: "session" };
const $ = selector => document.querySelector(selector);
const fmtPrice = value => Number(value).toFixed(4);
const chicagoDate = iso => new Intl.DateTimeFormat("en-CA", { timeZone: "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(iso));
const timeLabel = iso => new Intl.DateTimeFormat("en-US", { timeZone: "America/Chicago", hour: "numeric", minute: "2-digit" }).format(new Date(iso));
const dayLabel = date => new Intl.DateTimeFormat("en-US", { timeZone: "America/Chicago", weekday: "short", month: "short", day: "numeric" }).format(new Date(`${date}T12:00:00-05:00`));

function renderBrief(data) {
  const { bias, lastWeek, quote } = data;
  const directionClass = bias.direction.toLowerCase();
  $("#bias-word").textContent = bias.direction;
  $("#bias-word").className = directionClass;
  $("#confidence-chip").textContent = `${bias.confidence} confidence`;
  const stackWords = bias.stack.map(item => `${item.label.replace("Previous ", "").replace("Prior ", "")} ${item.direction}`).join(" · ");
  $("#bias-summary").textContent = `${stackWords}. ${bias.location}. Treat ${bias.draw.toLowerCase()} as the current draw only after the Asian-session sequence confirms it.`;
  $("#week-low").textContent = fmtPrice(lastWeek.low);
  $("#week-high").textContent = fmtPrice(lastWeek.high);
  $("#midpoint-label").textContent = `Midpoint ${fmtPrice(lastWeek.midpoint)}`;
  const percent = Math.max(0, Math.min(100, ((quote.price - lastWeek.low) / (lastWeek.high - lastWeek.low)) * 100));
  $("#price-marker").style.left = `${percent}%`;
  $("#price-marker").title = `Current price ${fmtPrice(quote.price)}`;
  $("#draw-label").textContent = bias.draw;
  $("#draw-price").textContent = fmtPrice(bias.drawPrice);
  $("#confirmation-label").textContent = bias.confirmation;
  $("#invalidation-title").textContent = bias.direction === "Neutral" ? "Decision pivot" : "Bias weakens beyond";
  $("#invalidation-label").textContent = fmtPrice(bias.invalidation);
  $("#score-value").textContent = bias.score > 0 ? `+${bias.score}` : bias.score;
  $("#news-risk-label").textContent = `${bias.newsRisk} risk`;
  $("#risk-count").textContent = `${bias.todayEventCount} event${bias.todayEventCount === 1 ? "" : "s"}`;
  $("#current-price").textContent = fmtPrice(quote.price);
  $("#updated-label").textContent = `Updated ${timeLabel(data.meta.updatedAt)} CT`;

  $("#evidence-list").innerHTML = bias.stack.map(item => `
    <div class="evidence-item ${item.direction === "bullish" ? "bull" : "bear"}">
      <span class="evidence-bar" aria-hidden="true"></span>
      <div><strong>${item.label} · ${item.direction}</strong><span>Weight ${item.weight} · ${item.range}</span></div>
    </div>`).join("") + `
    <div class="evidence-item">
      <span class="evidence-bar" aria-hidden="true"></span>
      <div><strong>Dealing-range location</strong><span>${bias.location}</span></div>
    </div>`;
}

function renderForecastAudit(data) {
  const record = data.forecastAudit;
  const stats = data.forecastStats || {};
  const recordLabel = stats.directional
    ? `${stats.right} right · ${stats.wrong} wrong · ${stats.mixed} mixed · ${stats.strictHitRate}% strict`
    : "No completed directional forecasts yet";
  $("#forecast-record").textContent = recordLabel;
  if (!record) return;

  const forecast = record.forecast;
  $("#audit-lock").textContent = `LOCKED · ${record.tradeDate}`;
  $("#audit-lock").classList.add("locked");
  $("#frozen-direction").textContent = `${forecast.direction} · ${forecast.score > 0 ? "+" : ""}${forecast.score}/15`;
  $("#frozen-raid").textContent = forecast.expectedFirstRaid;
  $("#frozen-target").textContent = forecast.targetLabel;
  $("#frozen-target-price").textContent = forecast.targetPrice == null ? "No commitment" : fmtPrice(forecast.targetPrice);
  $("#frozen-invalidation").textContent = forecast.invalidationLabel;
  $("#frozen-invalidation-price").textContent = forecast.invalidationPrice == null ? "Confirmation required" : fmtPrice(forecast.invalidationPrice);
  $("#forecast-reason").textContent = `${forecast.reason} Captured ${timeLabel(record.capturedAt)} CT; these fields are immutable.`;

  const checkpointIds = { midnight: "#cp-midnight", london: "#cp-london", newYork: "#cp-newyork", final: "#cp-final" };
  Object.entries(checkpointIds).forEach(([key, selector]) => {
    const item = record.checkpoints?.[key];
    if (!item) return;
    const target = $(selector);
    target.textContent = item.status === "graded" ? "Complete" : item.firstSweep ? `First raid: ${item.firstSweep}` : "Recorded";
    target.closest(".checkpoint").classList.add("done");
  });

  if (record.result) {
    const result = record.result;
    const grade = result.grade.replace("-", " ");
    $("#forecast-grade").textContent = grade.toUpperCase();
    $("#forecast-grade").className = result.grade;
    $("#forecast-excursion").textContent = result.favorablePips == null ? "Observation recorded" : `+${result.favorablePips} / −${result.adversePips} pips`;
  }
}

function renderAsia(data) {
  const { asia, london, playbook } = data.sessions;
  if (!asia || asia.high == null) {
    $("#asia-verdict").textContent = "The current Asian range has not completed yet.";
    return;
  }
  $("#range-quality").textContent = `${asia.quality} range`;
  $("#asia-high").textContent = fmtPrice(asia.high);
  $("#asia-low").textContent = fmtPrice(asia.low);
  $("#asia-range-pips").textContent = `${asia.rangePips.toFixed(1)} pips`;
  $("#asia-quality-note").textContent = asia.qualityNote;
  $("#first-sweep").textContent = london.firstSweep || "None yet";
  $("#midnight-open").textContent = asia.midnightOpen ? fmtPrice(asia.midnightOpen) : "—";
  $("#asia-state").textContent = playbook.state;
  $("#asia-verdict").textContent = playbook.verdict;
  $("#asia-steps").innerHTML = playbook.steps.map(step => `<li>${step}</li>`).join("");
  $("#no-trade-rule").textContent = playbook.noTradeIf;
}

function renderBtmm(data) {
  const btmm = data.btmm;
  if (!btmm) return;
  const pattern = btmm.pattern;
  $("#btmm-expected").textContent = data.forecastAudit?.btmmAtCapture?.expectedPattern || btmm.expectedPattern;
  $("#btmm-pattern").textContent = `${pattern.name} · ${pattern.status}`;
  $("#btmm-pattern-note").textContent = pattern.shape
    ? `${pattern.shape}-shape, ${pattern.direction}; second extreme gap ${pattern.gapPips.toFixed(1)} pips. ${pattern.rule}`
    : pattern.rule;
  $("#btmm-stop-hunt").textContent = btmm.asiaStopHunt;
  $("#btmm-hod-lod").textContent = btmm.hodLod;
  $("#btmm-level").textContent = btmm.levelCount.label;
  $("#btmm-level-note").textContent = btmm.levelCount.direction === "inside"
    ? "Price remains inside the Asian box."
    : `${btmm.levelCount.distancePips.toFixed(1)} pips ${btmm.levelCount.direction} Asia; fixed ${btmm.levelCount.bandPips}-pip bands.`;
  $("#btmm-adr").textContent = btmm.adr.usedPercent == null ? "—" : `${btmm.adr.usedPercent.toFixed(0)}%`;
  $("#btmm-adr-note").textContent = `${btmm.adr.usedPips.toFixed(1)} pips used of ${btmm.adr.averagePips.toFixed(1)}-pip five-day ADR.`;
  const mayoSide = btmm.emas.mayoDistancePips >= 0 ? "above" : "below";
  $("#btmm-mayo").textContent = `${Math.abs(btmm.emas.mayoDistancePips).toFixed(1)} pips ${mayoSide}`;
  $("#btmm-mayo-note").textContent = `${fmtPrice(btmm.emas.mayo200)}${btmm.emas.recentMayoTouch ? " · touched within the last hour" : " · no recent touch"}`;
  $("#btmm-ema").textContent = `${btmm.emas.alignment} alignment`;
  $("#btmm-ema-note").textContent = `EMA13 ${fmtPrice(btmm.emas.ema13)} · EMA50 ${fmtPrice(btmm.emas.ema50)}`;
  $("#btmm-tdi").textContent = `${btmm.tdiProxy.state} · RSI ${btmm.tdiProxy.rsi13.toFixed(1)}`;
  $("#btmm-tdi-note").textContent = `RSI-13 versus 2-period signal ${btmm.tdiProxy.signal2.toFixed(1)}.`;
  $("#btmm-rrt").textContent = btmm.railroadTracks.active ? `${btmm.railroadTracks.direction} RRT` : "None active";
  $("#btmm-rrt-note").textContent = btmm.railroadTracks.note;
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

function activeLevels(data) {
  return data.levels.filter(level => {
    if (state.chartMode === "session") return ["session", "both"].includes(level.group);
    if (state.chartMode === "btmm") return level.group === "btmm";
    return ["htf", "both"].includes(level.group);
  });
}

function levelSwept(level, price) {
  if (level.side === "buy") return price >= level.price;
  if (level.side === "sell") return price <= level.price;
  return false;
}

function renderLevelStrip(data) {
  $("#level-strip").innerHTML = activeLevels(data).map(level => `
    <span class="level-pill ${level.side} ${levelSwept(level, data.quote.price) ? "swept" : ""}"><b>${level.key}</b>${fmtPrice(level.price)} · ${level.label}${levelSwept(level, data.quote.price) ? " · swept" : ""}</span>`).join("");
}

function chartInstruction(data) {
  if (state.chartMode === "session") {
    const playbook = data.sessions.playbook;
    return `${playbook.state}: ${playbook.verdict} Expected raid: ${playbook.expectedRaid}. First target: ${playbook.firstTarget}.`;
  }
  if (state.chartMode === "btmm") {
    const btmm = data.btmm;
    return `${btmm.pattern.name} (${btmm.pattern.status}). ${btmm.levelCount.label}; ADR used ${btmm.adr.usedPercent == null ? "—" : btmm.adr.usedPercent.toFixed(0) + "%"}. Mayo/EMA200 ${fmtPrice(btmm.emas.mayo200)}. These tags do not alter the ICT bias.`;
  }
  return `${data.bias.direction} top-down narrative. ${data.bias.location}. Primary draw: ${data.bias.draw} at ${fmtPrice(data.bias.drawPrice)}.`;
}

function drawChart(data) {
  const canvas = $("#price-chart");
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  let candles = data.candles.slice(-220);
  if (state.chartMode === "session" && data.sessions.asia?.start) {
    const start = new Date(data.sessions.asia.start).getTime() / 1000 - 1800;
    candles = data.candles.filter(candle => candle.t >= start);
  }
  const levels = activeLevels(data);
  if (!candles.length) { $("#chart-empty").hidden = false; return; }
  $("#chart-empty").hidden = true;
  const levelPrices = levels.map(level => level.price);
  const min = Math.min(...candles.map(c => c.l), ...levelPrices);
  const max = Math.max(...candles.map(c => c.h), ...levelPrices);
  const range = max - min || .001;
  const displayMin = min - range * .025;
  const displayMax = max + range * .025;
  const pad = { top: 18, right: 84, bottom: 28, left: 4 };
  const width = rect.width - pad.left - pad.right;
  const height = rect.height - pad.top - pad.bottom;
  const y = value => pad.top + ((displayMax - value) / (displayMax - displayMin)) * height;
  const step = width / candles.length;
  const x = stamp => pad.left + ((stamp - candles[0].t) / (candles[candles.length - 1].t - candles[0].t || 1)) * width;
  const body = Math.max(1, Math.min(5, step * .58));
  ctx.clearRect(0, 0, rect.width, rect.height);

  if (state.chartMode === "session") {
    const bands = [
      [data.sessions.asia, "rgba(111,157,255,.075)", "ASIA"],
      [data.sessions.london, "rgba(111,227,177,.055)", "LONDON"],
      [data.sessions.newYork, "rgba(255,203,102,.05)", "NEW YORK"],
    ];
    bands.forEach(([session, color, label]) => {
      if (!session?.start) return;
      const start = new Date(session.start).getTime() / 1000;
      const end = new Date(session.end).getTime() / 1000;
      const left = Math.max(pad.left, x(start));
      const right = Math.min(pad.left + width, x(end));
      if (right <= left) return;
      ctx.fillStyle = color; ctx.fillRect(left, pad.top, right - left, height);
      ctx.fillStyle = "#6f7b88"; ctx.font = "9px ui-monospace, monospace"; ctx.textAlign = "left"; ctx.fillText(label, left + 5, pad.top + 12);
    });
  }

  ctx.font = "10px ui-monospace, monospace";
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i++) {
    const value = displayMin + ((displayMax - displayMin) * i / 4);
    const py = y(value);
    ctx.strokeStyle = "rgba(141,153,167,.12)";
    ctx.beginPath(); ctx.moveTo(0, py); ctx.lineTo(rect.width - pad.right + 8, py); ctx.stroke();
    ctx.fillStyle = "#778391"; ctx.fillText(fmtPrice(value), rect.width - 3, py + 3);
  }

  const colors = { buy: "#6fe3b1", sell: "#ff7777", mid: "#ffcb66", open: "#6f9dff" };
  const labelPositions = levels.map(level => ({ level, py: y(level.price) })).sort((a, b) => a.py - b.py);
  let lastLabelY = -100;
  labelPositions.forEach(item => {
    item.labelY = Math.max(item.py, lastLabelY + 13);
    item.labelY = Math.min(item.labelY, rect.height - pad.bottom - 2);
    lastLabelY = item.labelY;
  });
  labelPositions.forEach(({ level, py, labelY }) => {
    const color = colors[level.side] || "#8d99a7";
    ctx.setLineDash(level.side === "open" ? [2, 3] : [5, 5]);
    ctx.strokeStyle = color; ctx.globalAlpha = levelSwept(level, data.quote.price) ? .28 : .72;
    ctx.beginPath(); ctx.moveTo(0, py); ctx.lineTo(rect.width - pad.right + 8, py); ctx.stroke();
    ctx.globalAlpha = 1; ctx.setLineDash([]); ctx.fillStyle = color; ctx.textAlign = "right";
    ctx.fillText(`${level.key} ${fmtPrice(level.price)}`, rect.width - 3, labelY + 3);
  });

  candles.forEach((candle, index) => {
    const px = pad.left + index * step + step / 2;
    const color = candle.c >= candle.o ? "#6fe3b1" : "#ff7777";
    ctx.strokeStyle = color; ctx.fillStyle = color;
    ctx.beginPath(); ctx.moveTo(px, y(candle.h)); ctx.lineTo(px, y(candle.l)); ctx.stroke();
    const top = y(Math.max(candle.o, candle.c));
    const bottom = y(Math.min(candle.o, candle.c));
    ctx.fillRect(px - body / 2, top, body, Math.max(1, bottom - top));
  });

  ctx.textAlign = "left"; ctx.fillStyle = "#778391";
  const first = new Date(candles[0].t * 1000);
  const last = new Date(candles[candles.length - 1].t * 1000);
  ctx.fillText(new Intl.DateTimeFormat("en-US", { weekday:"short", hour:"numeric", timeZone:"America/Chicago" }).format(first), 2, rect.height - 5);
  ctx.textAlign = "right";
  ctx.fillText(new Intl.DateTimeFormat("en-US", { weekday:"short", hour:"numeric", minute:"2-digit", timeZone:"America/Chicago" }).format(last), rect.width - pad.right, rect.height - 5);
  $("#chart-instruction").textContent = chartInstruction(data);
  renderLevelStrip(data);
}

function setupChartModes(data) {
  document.querySelectorAll(".chart-mode").forEach(button => button.addEventListener("click", () => {
    state.chartMode = button.dataset.mode;
    document.querySelectorAll(".chart-mode").forEach(item => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    drawChart(data);
  }));
}

async function init() {
  try {
    const response = await fetch("data/market.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`Data request failed: ${response.status}`);
    state.data = await response.json();
    renderBrief(state.data);
    renderForecastAudit(state.data);
    renderAsia(state.data);
    renderBtmm(state.data);
    renderCalendar(state.data);
    setupChartModes(state.data);
    drawChart(state.data);
    window.addEventListener("resize", () => drawChart(state.data));
  } catch (error) {
    console.error(error);
    $("#updated-label").textContent = "Brief unavailable";
    $("#chart-empty").hidden = false;
  }
}

init();
