const {test} = require('node:test');
const assert = require('node:assert/strict');
const {RequestActivity} = require('../experiments/browser-agent-probe/readiness');
test('fetch SSE is recognized from response evidence; only established streams are exempt', () => {
  const a=new RequestActivity(), early={}, late={};
  a.started(early);
  assert.deepEqual(a.counts(), {finite:0,streams:0,unknown:1});
  const before=a.streams();
  a.response({request:()=>early,headers:()=>({'content-type':'text/event-stream; charset=utf-8'})});
  assert.equal(a.blockers().length,0);
  assert.equal(a.blockers(before).length,1);
  const established=a.streams();
  a.started(late);a.response({request:()=>late,headers:()=>({'content-type':'text/event-stream'})});
  assert.equal(a.blockers(established).length,1);
});
test('finite responses remain pending until finish and failure removes them', async () => {
  const a=new RequestActivity(), request={};a.started(request);
  a.response({request:()=>request,headers:()=>({'content-type':'application/json'})});
  assert.equal(a.counts().finite,1);
  const waiting=a.wait(()=>!a.blockers().length,1000);
  setTimeout(()=>a.finished(request),10);
  assert.equal((await waiting).state,'ready');
  assert.equal(a.events.listenerCount('change'),0);
});
test('unknown work times out without being reclassified as background', async () => {
  const a=new RequestActivity();a.started({});
  assert.equal((await a.wait(()=>!a.blockers().length,15)).state,'timed_out');
  assert.equal(a.counts().unknown,1);
  assert.equal(a.events.listenerCount('change'),0);
});
test('a lifecycle change during an asynchronous predicate is not lost', async () => {
  const a=new RequestActivity(), r={};a.started(r);let first=true;
  const result=await a.wait(async()=>{if(first){first=false;a.finished(r);return false}return true},500);
  assert.equal(result.state,'ready');assert.ok(result.waited_ms<100);
});
test('receipts distinguish headers, completed bodies, failure, and temporal intervals', () => {
  const a=new RequestActivity(), earlier={}, saved={}, failed={};
  a.started(earlier);const mark=a.sequence;a.started(saved);
  a.response({request:()=>saved,headers:()=>({'content-type':'application/json'}),status:()=>200});
  assert.equal(a.since(mark).requests[0].state,'headers');
  assert.equal(a.since(mark).requests[0].status,200);
  a.finished(saved);const end=a.sequence;
  a.started(failed);a.finished(failed,true);
  assert.deepEqual(a.since(mark,end).requests.map(r=>r.state),['completed']);
  assert.deepEqual(a.since(mark).requests.map(r=>r.state),['completed','failed']);
  assert.equal(a.since(mark).association,'temporal');
});
test('bounded lifecycle history reports lost coverage explicitly', () => {
  const a=new RequestActivity();a.limit=2;
  for(let i=0;i<3;i++){const r={};a.started(r);a.finished(r)}
  assert.equal(a.since(0).truncated,true);
  assert.equal(a.since(1).truncated,false);
  assert.equal(a.records.size,2);
});
