/**
 * PredictorX Dashboard — Client-side JavaScript
 * Chart.js integration + API calls + WebSocket live updates.
 */

const API = '/api';
let ws = null;
let vixChart = null;
let pnlChart = null;

// ── Navigation ────────────────────────────────────────

document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
        btn.classList.add('active');
        const tab = document.getElementById('tab-' + btn.dataset.tab);
        if (tab) tab.classList.add('active');
        loadTabData(btn.dataset.tab);
    });
});

// ── Tab Data Loading ──────────────────────────────────

function loadTabData(tab) {
    switch (tab) {
        case 'dashboard': loadDashboard(); break;
        case 'opportunities': loadOpportunities(); break;
        case 'weather': loadWeather(); break;
        case 'tails': loadTails(); break;
        case 'whales': loadWhales(); break;
        case 'performance': loadPerformance(); break;
        case 'calibration': loadCalibration(); break;
    }
}

// ── Dashboard ─────────────────────────────────────────

async function loadDashboard() {
    try {
        const data = await fetchJSON('/dashboard');

        setText('stat-total', data.performance.total_predictions);
        setText('stat-accuracy', (data.performance.accuracy * 100).toFixed(1) + '%');

        const pnlEl = document.getElementById('stat-pnl');
        pnlEl.textContent = '$' + data.performance.total_pnl.toFixed(2);
        pnlEl.className = 'stat-value ' + (data.performance.total_pnl >= 0 ? 'positive' : 'negative');

        setText('stat-whales', data.whale_activity);

        // Update VIX badges
        if (data.vix.price) {
            setText('vix-badge', 'VIX: ' + data.vix.price.toFixed(1));
            const regimeBadge = document.getElementById('regime-badge');
            regimeBadge.textContent = data.vix.regime;
            regimeBadge.className = 'badge badge-' + regimeClass(data.vix.regime);
        }

        // Recent predictions table
        const body = document.getElementById('recent-body');
        body.innerHTML = '';
        for (const p of data.recent_predictions) {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${p.strategy}</td>
                <td>${truncate(p.market, 40)}</td>
                <td>${p.side.toUpperCase()}</td>
                <td class="edge">${(p.edge * 100).toFixed(1)}%</td>
                <td>${(p.confidence * 100).toFixed(0)}%</td>
                <td class="outcome-${p.outcome || 'pending'}">${p.outcome || 'pending'}</td>
            `;
            body.appendChild(row);
        }
    } catch (e) {
        console.error('Dashboard load error:', e);
    }
}

// ── Opportunities ─────────────────────────────────────

document.getElementById('btn-scan')?.addEventListener('click', loadOpportunities);

async function loadOpportunities() {
    const btn = document.getElementById('btn-scan');
    const status = document.getElementById('scan-status');
    if (btn) btn.disabled = true;
    if (status) status.textContent = 'Scanning...';

    try {
        const data = await fetchJSON('/opportunities?limit=15');
        if (status) status.textContent = `${data.count} opportunities found`;

        const list = document.getElementById('opportunities-list');
        list.innerHTML = '';

        for (const opp of data.opportunities) {
            const card = document.createElement('div');
            card.className = 'opp-card urgency-' + opp.urgency;
            card.innerHTML = `
                <div class="opp-header">
                    <span class="opp-title">${opp.market_title || opp.market_ticker}</span>
                    <span class="opp-rank">#${opp.rank}</span>
                </div>
                <div class="opp-meta">
                    <span>Strategy: ${opp.strategy}</span>
                    <span>Side: ${opp.side.toUpperCase()}</span>
                    <span class="edge">Edge: +${(opp.edge * 100).toFixed(1)}%</span>
                    <span>Confidence: ${(opp.confidence * 100).toFixed(0)}%</span>
                    ${opp.recommended_contracts > 0 ? `<span>Size: ${opp.recommended_contracts} @ $${opp.recommended_cost}</span>` : ''}
                </div>
                <ul class="opp-reasons">
                    ${opp.reasons.map(r => `<li>${r}</li>`).join('')}
                </ul>
            `;
            list.appendChild(card);
        }
    } catch (e) {
        if (status) status.textContent = 'Scan failed: ' + e.message;
    } finally {
        if (btn) btn.disabled = false;
    }
}

// ── Weather ───────────────────────────────────────────

document.getElementById('weather-city')?.addEventListener('change', loadWeather);

async function loadWeather() {
    const city = document.getElementById('weather-city')?.value || '';
    const params = city ? `?city=${city}` : '';

    try {
        const data = await fetchJSON('/weather' + params);
        const list = document.getElementById('weather-list');
        list.innerHTML = '';

        for (const p of data.predictions) {
            const card = document.createElement('div');
            card.className = 'opp-card';
            card.innerHTML = `
                <div class="opp-header">
                    <span class="opp-title">${p.city} &mdash; ${p.market_title || p.market_ticker}</span>
                </div>
                <div class="opp-meta">
                    <span>Consensus: ${p.consensus_high || '?'}°F</span>
                    <span>Agreement: ${(p.source_agreement * 100).toFixed(0)}%</span>
                    <span class="edge">Edge: +${(p.edge * 100).toFixed(1)}%</span>
                    <span>Confidence: ${(p.confidence * 100).toFixed(0)}%</span>
                    ${p.recommended_contracts > 0 ? `<span>Size: ${p.recommended_contracts} @ $${p.recommended_cost}</span>` : ''}
                </div>
            `;
            list.appendChild(card);
        }

        if (data.predictions.length === 0) {
            list.innerHTML = '<div class="info-box">No weather predictions available.</div>';
        }
    } catch (e) {
        console.error('Weather load error:', e);
    }
}

// ── S&P Tails ─────────────────────────────────────────

async function loadTails() {
    try {
        const data = await fetchJSON('/tails');

        // Regime info
        const info = document.getElementById('tail-regime-info');
        if (data.vix) {
            const regime = data.vix.regime;
            const label = data.regime_info?.label || '';
            info.innerHTML = `
                <strong>VIX: ${data.vix.price.toFixed(1)}</strong> (${regime})
                ${data.vix.spx_price ? ` | S&P 500: ${data.vix.spx_price.toLocaleString()}` : ''}
                <br><em>${label}</em>
            `;
        }

        // Tail predictions
        const list = document.getElementById('tails-list');
        list.innerHTML = '';

        for (const p of data.predictions) {
            const card = document.createElement('div');
            card.className = 'opp-card';
            card.innerHTML = `
                <div class="opp-header">
                    <span class="opp-title">&gt;${p.pct_drop}% Drop</span>
                    <span>SELL YES</span>
                </div>
                <div class="opp-meta">
                    <span>Historical: ${(p.historical_prob * 100).toFixed(2)}%</span>
                    <span>Market: ${(p.market_price * 100).toFixed(0)}%</span>
                    <span class="edge">Edge: +${(p.edge * 100).toFixed(1)}%</span>
                    ${p.recommended_contracts > 0 ? `<span>Size: ${p.recommended_contracts} @ $${p.recommended_cost}</span>` : ''}
                </div>
            `;
            list.appendChild(card);
        }

        // VIX chart
        loadVixChart();
    } catch (e) {
        console.error('Tails load error:', e);
    }
}

async function loadVixChart() {
    try {
        const data = await fetchJSON('/tails/history?hours=24');
        const ctx = document.getElementById('vix-chart')?.getContext('2d');
        if (!ctx) return;

        if (vixChart) vixChart.destroy();

        vixChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.snapshots.map(s => new Date(s.timestamp).toLocaleTimeString()),
                datasets: [{
                    label: 'VIX',
                    data: data.snapshots.map(s => s.vix),
                    borderColor: '#f0b429',
                    backgroundColor: 'rgba(240, 180, 41, 0.1)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 0,
                }],
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#8b8f9a', maxTicksLimit: 8 }, grid: { color: '#2d3140' } },
                    y: { ticks: { color: '#8b8f9a' }, grid: { color: '#2d3140' } },
                },
            },
        });
    } catch (e) {
        console.error('VIX chart error:', e);
    }
}

// ── Whales ────────────────────────────────────────────

async function loadWhales() {
    try {
        const data = await fetchJSON('/whales?hours=24');
        const list = document.getElementById('whale-signals');
        list.innerHTML = '';

        if (data.signals.length === 0) {
            list.innerHTML = '<div class="info-box">No whale activity in the last 24 hours.</div>';
            return;
        }

        for (const s of data.signals) {
            const card = document.createElement('div');
            card.className = 'opp-card';
            card.innerHTML = `
                <div class="opp-header">
                    <span class="opp-title">${s.alias} (${s.category})</span>
                    <span>${s.side} $${s.amount.toLocaleString()}</span>
                </div>
                <div class="opp-meta">
                    <span>Market: ${truncate(s.market, 50)}</span>
                    ${s.price ? `<span>Price: ${s.price}</span>` : ''}
                </div>
            `;
            list.appendChild(card);
        }
    } catch (e) {
        console.error('Whales load error:', e);
    }
}

// ── Performance ───────────────────────────────────────

async function loadPerformance() {
    try {
        // Load daily chart data
        const daily = await fetchJSON('/performance/daily?days=30');
        loadPnlChart(daily);

        // Load prediction history
        const hist = await fetchJSON('/performance/predictions?limit=50');
        const body = document.getElementById('perf-body');
        body.innerHTML = '';

        for (const p of hist.predictions) {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${p.created ? new Date(p.created).toLocaleDateString() : '--'}</td>
                <td>${p.strategy}</td>
                <td>${truncate(p.market, 35)}</td>
                <td>${p.side.toUpperCase()}</td>
                <td>${(p.edge * 100).toFixed(1)}%</td>
                <td class="outcome-${p.outcome || 'pending'}">${p.outcome || 'pending'}</td>
                <td class="${p.pnl >= 0 ? 'positive' : 'negative'}">${p.pnl != null ? '$' + p.pnl.toFixed(2) : '--'}</td>
            `;
            body.appendChild(row);
        }
    } catch (e) {
        console.error('Performance load error:', e);
    }
}

function loadPnlChart(data) {
    const ctx = document.getElementById('pnl-chart')?.getContext('2d');
    if (!ctx) return;

    if (pnlChart) pnlChart.destroy();

    pnlChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.daily.map(d => d.date),
            datasets: [{
                label: 'Cumulative P&L',
                data: data.daily.map(d => d.cumulative_pnl),
                borderColor: '#4f8ff7',
                backgroundColor: 'rgba(79, 143, 247, 0.1)',
                fill: true,
                tension: 0.3,
            }],
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                x: { ticks: { color: '#8b8f9a', maxTicksLimit: 10 }, grid: { color: '#2d3140' } },
                y: { ticks: { color: '#8b8f9a', callback: v => '$' + v }, grid: { color: '#2d3140' } },
            },
        },
    });
}

// ── Calibration ───────────────────────────────────────

async function loadCalibration() {
    try {
        const data = await fetchJSON('/calibration');
        setText('cal-markets', data.total_markets || 0);
        setText('cal-ece', data.ece != null ? data.ece.toFixed(4) : '--');
        setText('cal-brier', data.brier_score != null ? data.brier_score.toFixed(4) : '--');

        const biasEl = document.getElementById('cal-bias');
        if (data.city_bias && Object.keys(data.city_bias).length > 0) {
            biasEl.innerHTML = '<h3>City Bias</h3>';
            for (const [city, bias] of Object.entries(data.city_bias)) {
                biasEl.innerHTML += `<div>${city}: ${bias > 0 ? '+' : ''}${bias.toFixed(4)}</div>`;
            }
        }
    } catch (e) {
        console.error('Calibration load error:', e);
    }
}

// ── WebSocket ─────────────────────────────────────────

function connectWebSocket() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws/live`);

    ws.onopen = () => {
        const el = document.getElementById('ws-status');
        el.textContent = 'Live';
        el.className = 'badge badge-online';
    };

    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            if (msg.data?.vix?.price) {
                setText('vix-badge', 'VIX: ' + msg.data.vix.price.toFixed(1));
                const regimeBadge = document.getElementById('regime-badge');
                regimeBadge.textContent = msg.data.vix.regime;
                regimeBadge.className = 'badge badge-' + regimeClass(msg.data.vix.regime);
            }
        } catch (e) {}
    };

    ws.onclose = () => {
        const el = document.getElementById('ws-status');
        el.textContent = 'Offline';
        el.className = 'badge badge-offline';
        setTimeout(connectWebSocket, 5000);
    };

    ws.onerror = () => ws.close();
}

// ── Utilities ─────────────────────────────────────────

async function fetchJSON(path) {
    const resp = await fetch(API + path);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
}

function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function truncate(str, len) {
    return str && str.length > len ? str.substring(0, len) + '...' : (str || '--');
}

function regimeClass(regime) {
    if (['LOW'].includes(regime)) return 'low';
    if (['LOW_MED', 'MEDIUM'].includes(regime)) return 'medium';
    if (['HIGH', 'CRISIS'].includes(regime)) return 'high';
    return '';
}

// ── Init ──────────────────────────────────────────────

loadDashboard();
connectWebSocket();
