const API = '/api/v1/network/contract-route-analysis';
const fmt = (n, digits = 1) => n == null ? 'Insufficient Data' : Number(n).toLocaleString(undefined, {maximumFractionDigits:digits});
const fmtHours = n => n == null ? 'Insufficient Data' : `${fmt(n)} h`;
const fmtMoment = s => s == null ? 'Insufficient Data' : new Date(s).toLocaleString();
const fmtTravelRange = m => m.average_travel_hours == null ? 'Insufficient Data' : `Avg ${fmtHours(m.average_travel_hours)}<br>Min ${fmtHours(m.minimum_travel_hours)} · Max ${fmtHours(m.maximum_travel_hours)}`;
const fmtPriceRange = m => m.average_contract_vehicle_price == null ? 'Insufficient Data' : `Avg ${fmt(m.average_contract_vehicle_price)}<br>Min ${fmt(m.minimum_contract_vehicle_price)} · Max ${fmt(m.maximum_contract_vehicle_price)}`;
const statusClass = value => value === 'GOOD' || value === 'GOOD ROUTE' ? 'good' : value === 'POOR' || value === 'POOR ROUTE' ? 'poor' : value === 'NEEDS ATTENTION' ? 'attention' : 'insufficient';
function metric(value, suffix = '') { return value == null ? 'Insufficient Data' : `${fmt(value)}${suffix}`; }

const svgNS = 'http://www.w3.org/2000/svg';
const graphColors = {good:'#36c99a',attention:'#f0b84b',poor:'#ee776b',insufficient:'#9aa9b6'};
function svg(tag, attrs = {}, text = '') {
  const element = document.createElementNS(svgNS, tag);
  Object.entries(attrs).forEach(([key,value]) => element.setAttribute(key, value));
  if (text) element.textContent = text;
  return element;
}
function renderNetwork(container, graph) {
  container.replaceChildren();
  const canvas = document.createElement('div'); canvas.className = 'graph-canvas';
  const drawing = svg('svg', {class:'route-svg',viewBox:'0 0 1200 250',role:'img','aria-label':'NetworkX directed contract route graph'});
  const defs = svg('defs');
  Object.entries(graphColors).forEach(([key,color]) => {
    const marker = svg('marker',{id:`arrow-${key}`,viewBox:'0 0 10 10',refX:'8',refY:'5',markerWidth:'8',markerHeight:'8',orient:'auto-start-reverse'});
    marker.append(svg('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:color})); defs.append(marker);
  });
  drawing.append(defs);
  const centers = graph.nodes.map((_,index) => 115 + index * 323);
  graph.edges.forEach((edge,index) => {
    const sourceIndex = graph.nodes.findIndex(node => node.id === edge.source);
    const destinationIndex = graph.nodes.findIndex(node => node.id === edge.destination);
    if (sourceIndex < 0 || destinationIndex < 0) return;
    const key = statusClass(edge.status), color = graphColors[key] || graphColors.insufficient;
    const x1 = centers[sourceIndex] + 89, x2 = centers[destinationIndex] - 89, mid = (x1+x2)/2;
    const metrics = edge.metrics;
    drawing.append(svg('path',{d:`M ${x1} 181 L ${x2} 181`,fill:'none',stroke:color,'stroke-width':'3.5','stroke-linecap':'round','marker-end':`url(#arrow-${key})`}));
    drawing.append(svg('rect',{x:mid-137,y:12,width:274,height:101,rx:14,class:'edge-card'}));
    drawing.append(svg('circle',{cx:mid-108,cy:37,r:4.5,fill:color}));
    drawing.append(svg('text',{x:mid-96,y:41,class:'edge-status',fill:color},`LEG ${index+1}  /  ${edge.status}`));
    drawing.append(svg('text',{x:mid,y:68,class:'edge-metric'},`${fmt(metrics.trip_count,0)} contract trips  ·  ${fmtHours(metrics.average_travel_hours)}`));
    drawing.append(svg('text',{x:mid,y:91,class:'edge-detail'},`Utilization  ${metric(metrics.load_utilization_pct,'%')}`));
  });
  graph.nodes.forEach((node,index) => {
    const x = centers[index], group = svg('g',{class:'hub-node'});
    group.append(svg('rect',{x:x-89,y:146,width:178,height:71,rx:16,class:'hub-card'}));
    group.append(svg('circle',{cx:x-59,cy:181,r:13,class:'hub-icon'}));
    group.append(svg('circle',{cx:x-59,cy:181,r:4,class:'hub-dot'}));
    group.append(svg('text',{x:x+10,y:179,class:'hub-label'},node.label));
    group.append(svg('text',{x:x+10,y:199,class:'hub-sub'},`HUB 0${index+1} · ROUTE NODE`));
    drawing.append(group);
  });
  canvas.append(drawing); container.append(canvas);
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
    document.getElementById('period').textContent = `${data.period.start} → ${data.period.end}`;
    document.getElementById('trip-count').textContent = fmt(data.summary.trip_count, 0);
    document.getElementById('utilization').textContent = metric(data.summary.load_utilization_pct, '%');
    document.getElementById('travel-time').textContent = fmtHours(data.summary.average_travel_hours);
    document.getElementById('contract-price').textContent = metric(data.summary.average_contract_vehicle_price);
    renderNetwork(document.getElementById('network'), data.graph);
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
