import test from 'node:test';
import assert from 'node:assert/strict';
import {newStorm, checkStorm, directionTo, stormPoint} from './storm-scenario/config.js';
import {createStormClient, StormConnectionError} from './storm-scenario/client.js';

test('portable configuration checks inputs and expresses track in physical units', () => {
  const config = newStorm();
  assert.equal(checkStorm(config), null);
  config.origin.lon = -8;
  assert.equal(newStorm().origin.lon, -10);
  const east = {...config, bearing_deg: 90, speed_kmh: 50};
  const point = stormPoint(east, 2);
  assert.equal(point[1], config.origin.lat);
  assert.ok(Math.abs((point[0] - config.origin.lon) * 111.32 * Math.cos(config.origin.lat * Math.PI / 180) - 100) < 1e-8);
  assert.equal(directionTo(config, [config.origin.lon, config.origin.lat + 1]).bearing_deg, 0);
  assert.equal(directionTo(config, [config.origin.lon + 1, config.origin.lat]).bearing_deg, 90);
  for (const change of [{speed_kmh: ''}, {hours: 6.5}, {seed: true}, {radius_km: NaN}, {version: 2}, {start_time: '2026-01-01'}]) {
    assert.ok(checkStorm({...config, ...change}));
  }
});

const json = (value, status = 200) => new Response(JSON.stringify(value), {status, headers: {'content-type': 'application/json'}});

test('client supports another backend URL, polls, and sends only scenario parameters', async () => {
  const calls = [], statuses = [];
  const responses = [json({job_id: 'abc'}, 202), json({status: 'queued'}), json({status: 'running'}), json({status: 'complete', result: {frames: []}})];
  const client = createStormClient({baseUrl: 'https://example.test/weather/', pollMs: 1,
    fetchImpl: async (url, options) => {calls.push({url, options});return responses.shift();}});
  const parameters = {storm_config: newStorm(), crews: 2};
  const result = await client.run(parameters, {onStatus: status => statuses.push(status)});
  assert.deepEqual(result, {frames: []});
  assert.equal(calls[0].url, 'https://example.test/weather/runs');
  assert.deepEqual(JSON.parse(calls[0].options.body), {...parameters, mode: 'single'});
  assert.equal(calls[1].url, 'https://example.test/weather/runs/abc');
  assert.deepEqual(statuses, ['queued', 'running', 'complete']);
});

test('client reports server validation, queue limits, connection errors and job failures', async () => {
  for (const status of [400, 429]) {
    const client = createStormClient({fetchImpl: async () => json({error: 'Useful server message'}, status)});
    await assert.rejects(client.run({}), /Useful server message/);
  }
  const offline = createStormClient({fetchImpl: async () => {throw new TypeError('Network error');}});
  await assert.rejects(offline.run({}), StormConnectionError);
  let count = 0;
  const failed = createStormClient({fetchImpl: async () => ++count === 1 ? json({job_id: 'bad'}) : json({status: 'failed', error: 'Simulation failed'})});
  await assert.rejects(failed.run({}), /Simulation failed/);
});

test('unmount cancellation stops polling instead of creating orphan UI updates', async () => {
  const controller = new AbortController();
  let count = 0;
  const client = createStormClient({pollMs: 1, fetchImpl: async () => {
    if (++count === 1) return json({job_id: 'cancel'});
    controller.abort();return json({status: 'running'});
  }});
  await assert.rejects(client.run({}, {signal: controller.signal}), {name: 'AbortError'});
  assert.equal(count, 2);
});
