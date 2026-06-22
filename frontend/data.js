/* ============================================================
   API INTEGRATION
   ============================================================ */

const API_BASE = 'http://localhost:8000/api';

async function fetchAPI(endpoint) {
  try {
    const res = await fetch(`${API_BASE}${endpoint}`);
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error(`Failed to fetch ${endpoint}:`, err);
    return null;
  }
}

async function postAPI(endpoint, body) {
  try {
    const res = await fetch(`${API_BASE}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error(`Failed to post ${endpoint}:`, err);
    return null;
  }
}

/* ============================================================
   LIVE DATA
   ============================================================ */

let instancesData = [];
let cyclesData = [];
let costData = null;

const KPI_ICONS = {
  total: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="6" rx="1.5"/><rect x="3" y="14" width="18" height="6" rx="1.5"/></svg>`,
  running: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M9 12l2 2 4-4"/></svg>`,
  stopped: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/></svg>`,
  cost: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M12 7v10M9 9.5a2.5 2.5 0 012.5-1.5h1a2 2 0 010 4h-2a2 2 0 000 4h1.5a2.5 2.5 0 002.5-1.5"/></svg>`,
  actions: `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M13 2L3 14h8l-1 8 10-12h-8l1-8z"/></svg>`,
};

async function loadLiveInstances() {
  const data = await fetchAPI('/instances');
  if (!data) return [];
  
  return data.map(inst => ({
    id: inst.id,
    name: inst.name,
    type: inst.instance_type,
    region: 'us-east-1',
    cpu: 0,
    diskRead: 0,
    diskWrite: 0,
    cost: 0,
    protected: inst.tags.protected === 'true',
    status: inst.status === 'running' ? 'healthy' : 'stopped',
    running: inst.status === 'running',
  }));
}

async function loadLiveMetrics() {
  const data = await fetchAPI('/metrics');
  if (!data) return {};
  
  const metricsMap = {};
  data.instances.forEach(inst => {
    const cpu = inst.metrics.CpuUtilizationPercent;
    const diskRead = inst.metrics.DiskReadBytes;
    const diskWrite = inst.metrics.DiskWriteBytes;
    
    metricsMap[inst.id] = {
      cpu: cpu.length ? Math.round(cpu[cpu.length - 1]) : 0,
      diskRead: diskRead.length ? Math.round(diskRead[diskRead.length - 1] / 1024 / 1024) : 0,
      diskWrite: diskWrite.length ? Math.round(diskWrite[diskWrite.length - 1] / 1024 / 1024) : 0,
    };
  });
  
  return metricsMap;
}

async function loadLiveCycles() {
  const data = await fetchAPI('/cycles?limit=50');
  if (!data) return [];
  
  return data.map(cycle => ({
    time: new Date(cycle.timestamp).toLocaleString(),
    instance: cycle.instance_name,
    action: cycle.action,
    reason: cycle.reasoning,
    result: cycle.validated ? 'success' : 'failed',
    cycle: cycle.cycle,
  }));
}

async function loadLiveCost() {
  const data = await fetchAPI('/cost');
  if (!data) return null;
  
  return {
    total: data.total_instances,
    running: data.running_count,
    stopped: data.stopped_count,
    hourly: data.estimated_hourly_usd,
    monthly: (data.estimated_hourly_usd * 730).toFixed(2),
  };
}

async function loadKillSwitch() {
  const data = await fetchAPI('/killswitch');
  return data ? data.active : false;
}

async function toggleKillSwitch(active) {
  return await postAPI('/killswitch', { active });
}

async function refreshLiveData() {
  instancesData = await loadLiveInstances();
  
  const metrics = await loadLiveMetrics();
  instancesData.forEach(inst => {
    if (metrics[inst.id]) {
      inst.cpu = metrics[inst.id].cpu;
      inst.diskRead = metrics[inst.id].diskRead;
      inst.diskWrite = metrics[inst.id].diskWrite;
      inst.status = inst.cpu > 85 ? 'critical' : (inst.cpu > 60 ? 'warning' : 'healthy');
    }
  });
  
  costData = await loadLiveCost();
  cyclesData = await loadLiveCycles();
  
  const killSwitch = await loadKillSwitch();
  if (killSwitch) {
    window.agentStatus = 'stopped';
  }
}

const PROTECTED_INSTANCES = [];
