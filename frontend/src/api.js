// Thin client for the Flask API (see docs/ARCHITECTURE.md). If the backend is
// unreachable, callers fall back to the bundled data/sample_storm.json.

export class OfflineError extends Error {}

async function request(path, options = {}, timeoutMs = 120000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(path, {...options, signal: controller.signal});
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('Comparison timed out. Reduce scenarios or horizon and retry.');
    throw new OfflineError('Cannot reach the backend.');
  } finally {
    clearTimeout(timer);
  }
  // The Vite proxy answers in plain text when Flask is down.
  if (!(response.headers.get('content-type') || '').includes('application/json')) {
    throw new OfflineError(`Backend answered HTTP ${response.status} without JSON.`);
  }
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (HTTP ${response.status}).`);
  return body;
}

// Real storms the backend can replay, for the storm picker.
export const fetchStorms = () => request('/api/storms', {}, 5000);

// The shared GB decision engine. Keep an explicit seed for fair mitigation reruns.
export function simulate(scenario, options = {}) {
  return request('/api/storm-plan', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({...options, scenario}),
  }, 300000);
}

// Raw corridor overlay; the backend hybrid separately derives a relative signal.
export const fetchRiskLayers = () => request('/api/corridor-risk', {}, 5000);
export const fetchRiskLayer = id => request(`/api/corridor-risk?id=${encodeURIComponent(id)}`, {}, 30000);

export async function loadSample() {
  const module = await import('../../data/sample_storm.json');
  return module.default;
}
