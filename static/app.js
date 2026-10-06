const API = '/api/v1/network/contract-route-analysis';
const fmt = (n, digits = 1) => n == null ? 'Insufficient Data' : Number(n).toLocaleString(undefined, {maximumFractionDigits:digits});
const fmtHours = n => n == null ? 'Insufficient Data' : `${fmt(n)} h`;
const fmtMoment = s => s == null ? 'Insufficient Data' : new Date(s).toLocaleString();
const fmtTravelRange = m => m.average_travel_hours == null ? 'Insufficient Data' : `Avg ${fmtHours(m.average_travel_hours)}<br>Min ${fmtHours(m.minimum_travel_hours)} · Max ${fmtHours(m.maximum_travel_hours)}`;
const fmtPriceRange = m => m.average_contract_vehicle_price == null ? 'Insufficient Data' : `Avg ${fmt(m.average_contract_vehicle_price)}<br>Min ${fmt(m.minimum_contract_vehicle_price)} · Max ${fmt(m.maximum_contract_vehicle_price)}`;
const statusClass = value => value === 'GOOD' || value === 'GOOD ROUTE' ? 'good' : value === 'POOR' || value === 'POOR ROUTE' ? 'poor' : value === 'NEEDS ATTENTION' ? 'attention' : 'insufficient';
function metric(value, suffix = '') { return value == null ? 'Insufficient Data' : `${fmt(value)}${suffix}`; }

const svgNS = 'http://www.w3.org/2000/svg';
const graphColors = {good:'#2e9d59',attention:'#e9b949',poor:'#d64545',insufficient:'#8996a0'};
function renderNetwork(container, graph) {
  container.replaceChildren();
  if (!window.L) {
    const unavailable = document.createElement('div');
    unavailable.className = 'loading';
    unavailable.textContent = 'Map tiles could not be loaded. Check your internet connection and refresh.';
    container.append(unavailable);
    return;
  }
  const mapElement = document.createElement('div'); mapElement.id = 'route-map'; mapElement.setAttribute('role','application');
  mapElement.setAttribute('aria-label','Map of contract route hubs in Tamil Nadu');
  container.append(mapElement);
  const map = L.map(mapElement,{scrollWheelZoom:false,zoomControl:true}).setView([11.55,78.25],7);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{
    maxZoom:19,
    attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(map);
  const locations = {
    Coimbatore:[11.0168,76.9558], Salem:[11.6643,78.1460],
    Trichy:[10.7905,78.7047], Chennai:[13.0827,80.2707],
    Tiruchirappalli:[10.7905,78.7047]
  };
  const pointFor = node => locations[node.id] || locations[node.label];
  const points = new Map();
  const bounds = [];
  graph.nodes.forEach((node,index) => {
    const point = pointFor(node);
    if (!point) return;
    points.set(node.id,point); bounds.push(point);
    const marker = L.circleMarker(point,{radius:9,color:'#fff',weight:3,fillColor:'#167b55',fillOpacity:1});
    marker.bindTooltip(`<strong>${node.label}</strong><br><span class="map-hub-caption">ROUTE HUB ${String(index+1).padStart(2,'0')}</span>`,{permanent:true,direction:'top',offset:[0,-8],className:'route-city-label'});
    marker.addTo(map);
  });
  graph.edges.forEach((edge,index) => {
    const source = points.get(edge.source), destination = points.get(edge.destination);
    if (!source || !destination) return;
    const key = statusClass(edge.status), color = graphColors[key] || graphColors.insufficient;
    const metrics = edge.metrics;
    const routeLine = L.polyline([source,destination],{color,weight:5,opacity:.92,lineCap:'round'}).addTo(map);
    routeLine.bindPopup(`<strong>${edge.source} → ${edge.destination}</strong><br>${edge.status}<br>${fmt(metrics.trip_count,0)} contract trips<br>Avg travel: ${fmtHours(metrics.average_travel_hours)}<br>Utilization: ${metric(metrics.load_utilization_pct,'%')}`);
    if (L.polylineDecorator) {
      L.polylineDecorator(routeLine,{patterns:[{offset:'72%',repeat:0,symbol:L.Symbol.arrowHead({pixelSize:12,polygon:true,pathOptions:{color,fillOpacity:1,weight:1}})}]}).addTo(map);
    }
  });
  if (bounds.length) map.fitBounds(bounds,{padding:[48,48]});
}

async function load() {
  try {
    const [response, health] = await Promise.all([fetch(API), fetch('/api/v1/network/health')]);
    if (!response.ok) throw new Error('Analysis could not be loaded. Check the database settings and application log.');
    const data = await response.json();
    const db = await health.json();
    const dbStatus = document.getElementById('db-status');
    dbStatus.textContent = db.status === 'ok' ? 'PostgreSQL · read-only' : 'Database unavailable';
    dbStatus.parentElement.classList.toggle('online', db.status === 'ok');
    document.getElementById('trip-count').textContent = fmt(data.summary.trip_count, 0);
    document.getElementById('utilization').textContent = metric(data.summary.load_utilization_pct, '%');
    document.getElementById('travel-time').textContent = fmtHours(data.summary.average_travel_hours);
    document.getElementById('contract-price').textContent = metric(data.summary.average_contract_vehicle_price);
    renderNetwork(document.getElementById('network'), data.graph);
    document.getElementById('segments').innerHTML = data.segments.map(s => {
      const m = s.metrics;
      return `<tr><td>${s.source} → ${s.destination}</td><td><span class="pill ${statusClass(s.status)}">${s.status}</span></td><td>${fmt(m.trip_count,0)}</td><td>${metric(m.total_load)}</td><td>${metric(m.average_load)}</td><td>${metric(m.average_capacity)}</td><td>${metric(m.load_utilization_pct,'%')}</td><td>${fmtMoment(m.earliest_departure)}<br>— ${fmtMoment(m.latest_arrival)}</td><td>${fmtTravelRange(m)}</td><td>${fmtPriceRange(m)}</td></tr>`;
    }).join('');
  } catch (error) {
    document.getElementById('db-status').textContent = 'Database unavailable';
    document.getElementById('network').innerHTML = `<div class="loading">${error.message}</div>`;
    document.getElementById('segments').innerHTML = `<tr><td colspan="10" class="loading">${error.message}</td></tr>`;
  }
}
load();
