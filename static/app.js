const API = '/api/v1/network/contract-route-analysis';
const fmt = (n, digits = 1) => n == null ? 'Insufficient Data' : Number(n).toLocaleString(undefined, {maximumFractionDigits:digits});
const fmtHours = n => n == null ? 'Insufficient Data' : `${fmt(n)} h`;
const statusClass = value => value === 'GOOD' || value === 'GOOD ROUTE' ? 'good' : value === 'POOR' || value === 'POOR ROUTE' ? 'poor' : value === 'NEEDS ATTENTION' ? 'attention' : 'insufficient';
function metric(value, suffix = '') { return value == null ? 'Insufficient Data' : `${fmt(value)}${suffix}`; }

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
  const routeLines = new Map();
  const bounds = [];
  graph.nodes.forEach((node,index) => {
    const point = pointFor(node);
    if (!point) return;
    points.set(node.id,point); bounds.push(point);
    const marker = L.circleMarker(point,{radius:9,color:'#fff',weight:3,fillColor:'#167b55',fillOpacity:1});
    marker.bindTooltip(`<strong>${node.label}</strong><br><span class="map-hub-caption">ROUTE HUB ${String(index+1).padStart(2,'0')}</span>`,{permanent:true,direction:'top',offset:[0,-8],className:'route-city-label'});
    marker.addTo(map);
  });
  graph.edges.forEach(edge => {
    const source = points.get(edge.source), destination = points.get(edge.destination);
    if (!source || !destination) return;
    const key = statusClass(edge.status), color = graphColors[key] || graphColors.insufficient;
    const metrics = edge.metrics;
    const routeLine = L.polyline([source,destination],{color,weight:5,opacity:.92,lineCap:'round'}).addTo(map);
    routeLines.set(`${edge.source}|${edge.destination}`, routeLine);
    routeLine.bindPopup(`<strong>${edge.source} → ${edge.destination}</strong><br>${edge.status}<br>${fmt(metrics.trip_count,0)} contract trips<br>Avg travel: ${fmtHours(metrics.average_travel_hours)}<br>Utilization: ${metric(metrics.load_utilization_pct,'%')}`);
    if (L.polylineDecorator) {
      L.polylineDecorator(routeLine,{patterns:[{offset:'72%',repeat:0,symbol:L.Symbol.arrowHead({pixelSize:12,polygon:true,pathOptions:{color,fillOpacity:1,weight:1}})}]}).addTo(map);
    }
  });
  if (bounds.length) map.fitBounds(bounds,{padding:[48,48]});
  renderAlternatives(document.getElementById('route-options'), graph, routeLines, points, map);
}

function renderAlternatives(container, graph, routeLines, points, map) {
  container.replaceChildren();
  if (!graph.alternatives?.length) {
    const empty = document.createElement('p');
    empty.className = 'route-options-empty';
    empty.textContent = 'No historical Coimbatore-to-Chennai route combinations were found.';
    container.append(empty);
    return;
  }
  const heading = document.createElement('div');
  heading.className = 'route-options-heading';
  heading.textContent = `HISTORICAL ROUTE COMBINATIONS · ${graph.alternatives.length}`;
  container.append(heading);
  const list = document.createElement('div'); list.className = 'route-option-list';
  graph.alternatives.forEach((option,index) => {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'route-option';
    const top = document.createElement('span'); top.className = 'route-option-top';
    const route = document.createElement('strong'); route.textContent = option.cities.join('  →  ');
    const status = document.createElement('span'); status.className = `pill ${statusClass(option.status)}`; status.textContent = option.status;
    top.append(route,status);
    const legList = document.createElement('span'); legList.className = 'route-option-legs';
    option.legs.forEach(leg => {
      const item = document.createElement('span'); item.className = 'route-option-leg';
      const dot = document.createElement('i'); dot.className = `route-status-dot ${statusClass(leg.status)}`;
      const text = document.createElement('span');
      text.textContent = `${leg.source} → ${leg.destination}: ${fmt(leg.metrics.trip_count,0)} trips`;
      item.append(dot,text); legList.append(item);
    });
    button.append(top,legList);
    button.addEventListener('click',() => {
      list.querySelectorAll('.route-option').forEach(item => item.classList.remove('active'));
      button.classList.add('active');
      routeLines.forEach(line => line.setStyle({opacity:.16,weight:3}));
      const selectedPoints = [];
      option.legs.forEach(leg => {
        routeLines.get(`${leg.source}|${leg.destination}`)?.setStyle({opacity:1,weight:7});
        const start = points.get(leg.source), end = points.get(leg.destination);
        if (start) selectedPoints.push(start);
        if (end) selectedPoints.push(end);
      });
      if (selectedPoints.length) map.fitBounds(selectedPoints,{padding:[55,55],maxZoom:8});
    });
    if (index === 0) button.classList.add('active');
    list.append(button);
  });
  container.append(list);
  if (graph.alternatives[0]) {
    graph.edges.forEach(edge => routeLines.get(`${edge.source}|${edge.destination}`)?.setStyle({opacity:.16,weight:3}));
    graph.alternatives[0].legs.forEach(leg => routeLines.get(`${leg.source}|${leg.destination}`)?.setStyle({opacity:1,weight:7}));
  }
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
    renderNetwork(document.getElementById('network'), {...data.graph, alternatives:data.alternatives});
    document.getElementById('alternatives').innerHTML = (data.alternatives || []).map(option => {
      const legTrips = option.legs.map(leg => `${leg.source} → ${leg.destination}: ${fmt(leg.metrics.trip_count,0)}`).join('<br>');
      const movements = option.legs.reduce((total, leg) => total + leg.metrics.trip_count, 0);
      return `<tr><td>${option.cities.join(' → ')}</td><td><span class="pill ${statusClass(option.status)}">${option.status}</span></td><td class="leg-trip-list">${legTrips}</td><td>${fmt(movements,0)}</td><td>${fmtHours(option.average_full_route_travel_hours)}</td><td>${metric(option.average_full_route_contract_price)}</td></tr>`;
    }).join('') || '<tr><td colspan="6" class="loading">No route combinations with trips were found.</td></tr>';
  } catch (error) {
    document.getElementById('db-status').textContent = 'Database unavailable';
    document.getElementById('network').innerHTML = `<div class="loading">${error.message}</div>`;
    document.getElementById('route-options').textContent = '';
    document.getElementById('alternatives').innerHTML = `<tr><td colspan="6" class="loading">${error.message}</td></tr>`;
  }
}
load();
