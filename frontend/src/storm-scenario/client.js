// Portable HTTP client. Inject fetch/baseUrl when mounting in another frontend.
export class StormConnectionError extends Error {}

function pause(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason || new DOMException('Aborted', 'AbortError'));
    const abort = () => {clearTimeout(timer);reject(signal.reason || new DOMException('Aborted', 'AbortError'));};
    const timer = setTimeout(() => {signal?.removeEventListener('abort', abort);resolve();}, ms);
    signal?.addEventListener('abort', abort, {once: true});
  });
}

export function createStormClient({baseUrl = '/api/storm-scenarios', fetchImpl = globalThis.fetch, pollMs = 600} = {}) {
  const base = baseUrl.replace(/\/$/, '');
  async function request(path, {signal, ...options} = {}) {
    let response;
    try {
      response = await fetchImpl(`${base}${path}`, {...options, signal});
    } catch (error) {
      if (signal?.aborted || error.name === 'AbortError') throw error;
      throw new StormConnectionError('Cannot reach the storm simulation server.');
    }
    if (!(response.headers.get('content-type') || '').includes('application/json')) {
      throw new StormConnectionError('Storm server did not return JSON.');
    }
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || `Storm request failed (HTTP ${response.status}).`);
    return body;
  }
  return {
    contract: options => request('', options),
    async run(parameters, {mode = 'single', signal, onStatus = () => {}} = {}) {
      const timeout = AbortSignal.timeout(300000);
      const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
      const {job_id: id} = await request('/runs', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({...parameters, mode}), signal: combined,
      });
      for (;;) {
        const job = await request(`/runs/${encodeURIComponent(id)}`, {signal: combined});
        onStatus(job.status);
        if (job.status === 'complete') return job.result;
        if (job.status === 'failed') throw new Error(job.error || 'Storm simulation failed.');
        if (!['queued', 'running'].includes(job.status)) throw new Error('Unexpected storm job status.');
        await pause(pollMs, combined);
      }
    },
  };
}
