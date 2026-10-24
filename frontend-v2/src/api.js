// Thin client for the Flask API (see docs/ARCHITECTURE.md). If the backend is
// unreachable, callers fall back to the bundled data/sample_run.json.

export class OfflineError extends Error {}

async function request(path, options = {}, timeoutMs = 120000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(path, {...options, signal: controller.signal});
  } catch {
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

export const fetchNetwork = () => request('/api/network', {}, 5000);

// A fresh seed per click gives a different storm each time; everything else
// uses the backend's defaults.
export function simulate() {
  return request('/api/experiment', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({seed: Math.floor(Math.random() * 1_000_000)}),
  });
}

export async function loadSample() {
  const module = await import('../../data/sample_run.json');
  return module.default;
}
