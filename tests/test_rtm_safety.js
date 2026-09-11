// Run with: node --test tests/test_rtm_safety.js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/basicRtm.js', 'utf8');

async function harness(send) {
  const timers = new Map();let next=0;
  const client={on(){},login:async()=>{},createChannel:()=>({on(){},join:async()=>{}}),sendMessageToPeer:send};
  const ctx={window:{},document:{},console:{log(){}},
    $:arg=>typeof arg==='object'?{ready:cb=>cb()}:{val:()=>"mock"},
    AgoraRTM:{createInstance:()=>client},
    setTimeout(fn){const id=++next;timers.set(id,fn);return id;},clearTimeout(id){timers.delete(id);}};
  vm.createContext(ctx);vm.runInContext(source,ctx);
  await new Promise(setImmediate);
  return {api:ctx.window,timers};
}
const stop={linear:0,angular:0};
test('safety send waits for transport and propagates asynchronous rejection',async()=>{
  let reject;const h=await harness(()=>new Promise((_,no)=>{reject=no;}));
  let settled=false;const send=h.api.sendMessageAwait(stop).finally(()=>{settled=true;});
  await Promise.resolve();assert.equal(settled,false);
  reject(new Error('transport failed'));await assert.rejects(send,/transport failed/);
  assert.equal(h.api.rtmHealth().failed,1);assert.equal(h.timers.size,0);
});
test('only an explicit receipt confirms peer delivery',async()=>{
  for(const result of [{hasPeerReceived:false}]){
    const h=await harness(async()=>result);
    assert.equal(await h.api.sendMessageAwait(stop),false);
    assert.equal(h.api.rtmHealth().delivered,0);assert.equal(h.api.rtmHealth().unconfirmed,1);
  }
  const h=await harness(async()=>({hasPeerReceived:true}));
  assert.equal(await h.api.sendMessageAwait(stop),true);
  assert.equal(h.api.rtmHealth().delivered,1);
});
test('pending safety send times out',async()=>{
  const h=await harness(()=>new Promise(()=>{}));const send=h.api.sendMessageAwait(stop);
  [...h.timers.values()][0]();await assert.rejects(send,/unreachable/);
  assert.equal(h.timers.size,0);
});
test('ordinary control dispatch stays nonblocking',async()=>{
  const h=await harness(()=>new Promise(()=>{}));
  assert.equal(h.api.sendMessage({linear:1}),true);
});

test('missing delivery result is a failure, not transport acceptance',async()=>{
  for(const result of [{},undefined]){
    const h=await harness(async()=>result);
    await assert.rejects(h.api.sendMessageAwait(stop),/no delivery result/);
    assert.equal(h.api.rtmHealth().unconfirmed,0);
  }
});
