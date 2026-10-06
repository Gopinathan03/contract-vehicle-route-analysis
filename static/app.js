const API = '/api/v1/network/contract-route-analysis';
const fmt = (n, digits = 1) => n == null ? 'Insufficient Data' : Number(n).toLocaleString(undefined, {maximumFractionDigits:digits});
const fmtHours = n => n == null ? 'Insufficient Data' : `${fmt(n)} h`;
const fmtMoment = s => s == null ? 'Insufficient Data' : new Date(s).toLocaleString();
const fmtTravelRange = m => m.average_travel_hours == null ? 'Insufficient Data' : `Avg ${fmtHours(m.average_travel_hours)}<br>Min ${fmtHours(m.minimum_travel_hours)} · Max ${fmtHours(m.maximum_travel_hours)}`;
const fmtPriceRange = m => m.average_contract_vehicle_price == null ? 'Insufficient Data' : `Avg ${fmt(m.average_contract_vehicle_price)}<br>Min ${fmt(m.minimum_contract_vehicle_price)} · Max ${fmt(m.maximum_contract_vehicle_price)}`;
const statusClass = value => value === 'GOOD' || value === 'GOOD ROUTE' ? 'good' : value === 'POOR' || value === 'POOR ROUTE' ? 'poor' : value === 'NEEDS ATTENTION' ? 'attention' : 'insufficient';
function metric(value, suffix = '') { return value == null ? 'Insufficient Data' : `${fmt(value)}${suffix}`; }

async function load() {
  try {
    const [response, health] = await Promise.all([fetch(API), fetch('/api/v1/network/health')]);
    if (!response.ok) throw new Error('Analysis could not be loaded. Check the database settings and application log.');
    const data = await response.json();
    const db = await health.json();
    const dbStatus = document.getElementById('db-status');
    dbStatus.textContent = db.status === 'ok' ? 'PostgreSQL · read-only' : 'Database unavailable';
    dbStatus.parentElement.classList.toggle('online', db.status === 'ok');
    document.getElementById('period').textContent = `${data.period.start} → ${data.period.end}`;
    document.getElementById('trip-count').textContent = fmt(data.summary.trip_count, 0);
    document.getElementById('utilization').textContent = metric(data.summary.load_utilization_pct, '%');
    document.getElementById('travel-time').textContent = fmtHours(data.summary.average_travel_hours);
    document.getElementById('contract-price').textContent = metric(data.summary.average_contract_vehicle_price);
    const network = document.getElementById('network');
    network.replaceChildren();
    data.route.forEach((hub, index) => {
      const nodeWrap = document.createElement('div'); nodeWrap.className = 'node-wrap';
      const node = document.createElement('div'); node.className = 'node';
      node.innerHTML = `<div class="node-dot"></div><div class="node-label">${hub}</div>`;
      nodeWrap.append(node);
      if (index < data.segments.length) {
        const seg = data.segments[index]; const edge = document.createElement('div'); edge.className = 'edge';
        edge.innerHTML = `<div class="edge-meta"><strong>${seg.status}</strong><small>${fmt(seg.metrics.trip_count,0)} trips</small></div>`;
        nodeWrap.append(edge);
      }
      network.append(nodeWrap);
    });
    document.getElementById('segments').innerHTML = data.segments.map(s => {
      const m = s.metrics;
      return `<tr><td>${s.source} → ${s.destination}</td><td><span class="pill ${statusClass(s.status)}">${s.status}</span></td><td>${fmt(m.trip_count,0)}</td><td>${metric(m.total_load)}</td><td>${metric(m.average_load)}</td><td>${metric(m.average_capacity)}</td><td>${metric(m.load_utilization_pct,'%')}</td><td>${fmtMoment(m.earliest_departure)}<br>— ${fmtMoment(m.latest_arrival)}</td><td>${fmtTravelRange(m)}</td><td>${fmtPriceRange(m)}</td></tr>`;
    }).join('');
    const recommendation = document.getElementById('route-status'); recommendation.textContent = data.recommendation.status; recommendation.className = `status-pill ${statusClass(data.recommendation.status)}`;
    document.getElementById('route-reason').textContent = data.recommendation.reason;
    document.getElementById('notes').innerHTML = data.data_notes.map(note => `<li>${note}</li>`).join('');
  } catch (error) {
    document.getElementById('db-status').textContent = 'Database unavailable';
    document.getElementById('period').textContent = 'Unable to load';
    document.getElementById('network').innerHTML = `<div class="loading">${error.message}</div>`;
    document.getElementById('segments').innerHTML = `<tr><td colspan="10" class="loading">${error.message}</td></tr>`;
    document.getElementById('route-reason').textContent = error.message;
  }
}
load();
