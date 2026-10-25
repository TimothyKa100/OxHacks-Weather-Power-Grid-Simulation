import test from 'node:test';
import assert from 'node:assert/strict';
import {portfolioRun, sameStormRequest} from './planning.js';
import {checkRun} from './schema.js';
import {buildTimeline, frameIndexAt} from './timeline.js';

const f=(hour,status,served)=>({hour,status:[status],served_by_bus:[served],events:[],failure_probability:[.1],islands:[[1]],gust_kmh:[100]});
function fixture(){
  return {network:{buses:[{id:1,x:0,y:0,load_mw:10}],branches:[{id:'L001',from:1,to:1}]},
    frames:[{...f(0,'online',10),storm:null},{...f(1,'weather',0),storm:[.2,.3],storm_radius:.1}],
    replay:{request:{scenario:'synthetic',seed:7,hours:6,budget:2}},
    plan:{portfolios:{optimized:{playback:{frames:[f(0,'online',10),f(1,'online',10),f(1,'overload',0),f(2,'online',10)]}}}}};
}

test('map follows chosen portfolio, settles cascade and shows restoration',()=>{
  const original=fixture();
  const chosen=portfolioRun(original,'optimized');
  assert.equal(chosen.frames.length,3);
  assert.equal(chosen.frames[1].status[0],'overload');
  assert.equal(chosen.frames[2].status[0],'online');
  assert.deepEqual(chosen.frames[1].storm,[.2,.3]);
  assert.deepEqual(chosen.frames[1].fallen_at,[null]);
  assert.equal(checkRun(chosen),null);
  assert.equal(original.frames[1].status[0],'weather');
  const timeline=buildTimeline(chosen);
  assert.equal(timeline.frames[frameIndexAt(timeline,1.6)].status[0],'online');
});

test('rerun copies completed storm settings rather than new picker values',()=>{
  const original=fixture(), draft=['L001'];
  const request=sameStormRequest(original,draft,{expected_events:2});
  draft.push('L002');
  assert.equal(request.seed,7);
  assert.equal(request.scenario,'synthetic');
  assert.deepEqual(request.custom_lines,['L001']);
  assert.equal(request.economics.expected_events,2);
});

test('legacy sample remains viewable and has no fabricated decision plan',()=>{
  const sample=fixture();delete sample.plan;
  assert.equal(portfolioRun(sample,'custom'),sample);
  assert.throws(()=>sameStormRequest(sample,[],{}));
});
