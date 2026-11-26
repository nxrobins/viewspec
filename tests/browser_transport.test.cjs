const {test} = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const net = require('node:net');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {startProxy} = require('../experiments/browser-agent-probe/comparison/transport');
const {wsServer: WebSocketServer, ws: WebSocket} = require('../experiments/browser-agent-probe/node_modules/playwright-core/lib/utilsBundle');
const delay = ms => new Promise(r=>setTimeout(r,ms));

async function setup(t, options={}) {
  const sockets=new Set();let streams=0;
  const server=http.createServer(async(req,res)=>{
    if(req.url==='/events') {
      streams++;res.writeHead(200,{'content-type':'text/event-stream'});res.flushHeaders();
      const timer=setInterval(()=>res.write('data: hi\n\n'),20);
      res.once('close',()=>{clearInterval(timer);streams--});return;
    }
    if(req.url==='/chunks') {
      res.writeHead(200,{'content-type':'text/plain','trailer':'x-done'});res.write('first');
      setTimeout(()=>{res.addTrailers({'x-done':'yes'});res.end('last')},120);return;
    }
    if(req.url==='/head'){res.writeHead(200,{'content-length':'123'});res.end();return}
    const chunks=[];for await(const c of req)chunks.push(c);
    res.writeHead(200,{'content-type':'application/octet-stream','set-cookie':['a=1','b=2'],'x-cookie':req.headers.cookie||''});res.end(Buffer.concat(chunks));
  });
  server.on('connection',s=>{sockets.add(s);s.once('close',()=>sockets.delete(s))});
  const ws=new WebSocketServer({noServer:true,perMessageDeflate:true});
  server.on('upgrade',(req,socket,head)=>ws.handleUpgrade(req,socket,head,client=>client.on('message',(data,binary)=>client.send(data,{binary}))));
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'viewspec-transport-'));
  const proxy=await startProxy({upstream:`http://127.0.0.1:${server.address().port}`,directory,methods:['GET','HEAD','POST','WEBSOCKET'],...options});
  let stopped=false;
  const stop=async()=>{if(!stopped){stopped=true;return await proxy.stop()}};
  t.after(async()=>{await stop();for(const c of ws.clients)c.terminate();for(const s of sockets)s.destroy();await new Promise(r=>server.close(r));fs.rmSync(directory,{recursive:true,force:true})});
  return {...proxy,stop,directory,streams:()=>streams,events:()=>fs.readFileSync(path.join(directory,'events.jsonl'),'utf8').trim().split('\n').map(JSON.parse)};
}

test('SSE arrives incrementally and client cancellation closes upstream',async t=>{
  const p=await setup(t);let events=0;
  await new Promise((resolve,reject)=>http.get(p.origin+'/events',res=>res.on('data',()=>{if(++events===3){res.destroy();resolve()}})).on('error',reject));
  for(let i=0;i<50&&p.streams();i++)await delay(10);
  assert.equal(p.streams(),0);await p.stop();
  assert.ok(p.events().some(e=>e.event==='client_closed'));
  assert.equal(p.events().at(-1).valid,true);
});
test('binary requests, cookies, chunks, trailers and HEAD survive forwarding',async t=>{
  const p=await setup(t,{cookie:'session=fixture; same=old'}), bytes=Buffer.from([0,255,128,42]);
  const r=await fetch(p.origin+'/echo',{method:'POST',body:bytes,headers:{cookie:'same=new'}});
  assert.deepEqual(Buffer.from(await r.arrayBuffer()),bytes);
  assert.deepEqual(r.headers.getSetCookie(),['a=1','b=2']);assert.equal(r.headers.get('x-cookie'),'session=fixture; same=new');
  assert.equal((await fetch(p.origin+'/head',{method:'HEAD'})).headers.get('content-length'),'123');
  const parts=[];const trailers=await new Promise((resolve,reject)=>http.get(p.origin+'/chunks',res=>{assert.equal(res.headers.trailer,'x-done');res.on('data',c=>parts.push(c.toString()));res.on('end',()=>resolve(res.trailers))}).on('error',reject));
  assert.deepEqual(parts,['first','last']);assert.equal(trailers['x-done'],'yes');
  await p.stop();const body=p.events().find(e=>e.event==='body'&&e.direction==='request'&&e.bytes===4);
  assert.deepEqual(fs.readFileSync(path.join(p.directory,body.file)),bytes);assert.equal(body.complete,true);
});
test('off-origin absolute URLs and CONNECT are refused; methods are scoped',async t=>{
  const p=await setup(t,{methods:['GET','HEAD']});
  const request=(method,url)=>new Promise((resolve,reject)=>{
    const r=http.request({hostname:'127.0.0.1',port:new URL(p.origin).port,method,path:url,headers:{host:new URL(p.origin).host}},res=>{res.resume();resolve(res.statusCode)});
    r.on('connect',(res,s)=>{s.destroy();resolve(res.statusCode)});r.on('error',reject);r.end();
  });
  assert.equal(await request('GET','http://127.0.0.1:1/escape'),403);
  assert.equal(await request('CONNECT','127.0.0.1:1'),403);
  assert.equal(await request('POST','/echo'),403);
});
for(const tunneled of [false,true]) test(`WebSocket binary, fragmentation and compression through ${tunneled?'CONNECT':'reverse proxy'}`,async t=>{
  const p=await setup(t);
  let agent;
  if(tunneled){
    agent=new http.Agent();
    agent.createConnection=(options,callback)=>{
      const request=http.request({hostname:'127.0.0.1',port:new URL(p.origin).port,method:'CONNECT',path:new URL(p.origin).host});
      request.once('connect',(response,socket)=>{assert.equal(response.statusCode,200);callback(null,socket)});
      request.once('error',callback);request.end();
    };
    t.after(()=>agent.destroy());
  }
  const socket=new WebSocket(p.origin.replace('http:','ws:')+'/ws',{perMessageDeflate:true,agent});
  await new Promise((resolve,reject)=>{socket.once('open',resolve);socket.once('error',reject)});
  const answer=new Promise((resolve,reject)=>{socket.once('message',resolve);socket.once('error',reject)});
  socket.send(Buffer.from([0,255]),{fin:false});socket.send(Buffer.from([128,42]),{fin:true});
  assert.deepEqual(Buffer.from(await answer),Buffer.from([0,255,128,42]));
  const closed=new Promise(r=>socket.once('close',r));socket.close(1000);await closed;await p.stop();
  const wire=p.events().filter(e=>e.event==='body'&&e.direction.startsWith('ws-'));
  assert.equal(wire.length,2);assert.ok(wire.every(e=>e.bytes>0&&fs.statSync(path.join(p.directory,e.file)).size===e.bytes));
  assert.equal(p.events().at(-1).valid,true);
});
test('same-origin CONNECT re-enters scoped HTTP handling; nested tunnels are refused',async t=>{
  const p=await setup(t), host=new URL(p.origin).host;
  const socket=net.connect({host:'127.0.0.1',port:new URL(p.origin).port});let data='';
  const read=()=>new Promise((resolve,reject)=>{const onData=c=>{data+=c;if(data.includes('\r\n\r\n')){socket.off('data',onData);const result=data;data='';resolve(result)}};socket.on('data',onData);socket.once('error',reject)});
  let response=read();socket.write(`CONNECT ${host} HTTP/1.1\r\nHost: ${host}\r\n\r\n`);assert.match(await response,/200 Connection Established/);
  response=read();socket.write(`CONNECT ${host} HTTP/1.1\r\nHost: ${host}\r\n\r\n`);assert.match(await response,/403 Forbidden/);socket.destroy();
});
test('audit overflow invalidates an attempt instead of silently truncating evidence',async t=>{
  const p=await setup(t,{maxBytes:2});await fetch(p.origin+'/echo',{method:'POST',body:'too large'}).catch(()=>{});
  const result=await p.stop();assert.equal(result.valid,false);assert.ok(p.events().some(e=>e.event==='audit_limit'));
});
