// Run with: node --test tests/test_arena_lifecycle.js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/arenaCameras.js', 'utf8');

function harness() {
  const timers = new Map(), clients = [];
  let nextTimer = 0;
  let payload = {APP_ID:'venue', CHANNEL_NAME:'arena', RTC_TOKEN:'fresh', USERID:42, CAMERAS:{1:1001}};
  let failure = false;
  const ctx = {
    window:{}, document:{readyState:'loading', addEventListener(){}, getElementById(){return null;}},
    console:{log(){}, error(){}}, AbortController,
    setTimeout(fn, delay){const id=++nextTimer;timers.set(id,{fn,delay});return id;},
    clearTimeout(id){timers.delete(id);},
    fetch:async()=>({ok:!failure,status:503,json:async()=>payload}),
    AgoraRTC:{createClient(){
      const client = {events:{},joins:0,renews:0,leaves:0,connectionState:'DISCONNECTED',
        on(name,fn){this.events[name]=fn;}, removeAllListeners(){this.events={};},
        async setClientRole(){}, async join(){this.joins++;this.connectionState='CONNECTED';},
        async renewToken(){this.renews++;}, async leave(){this.leaves++;this.connectionState='DISCONNECTED';}};
      clients.push(client);return client;
    }},
  };
  vm.createContext(ctx);vm.runInContext(source,ctx);
  return {ctx,clients,timers,setFailure(value){failure=value;},setPayload(value){payload={...payload,...value};},
    async retry(){
      const entry=[...timers].find(([,value])=>value.delay<18000);
      assert.ok(entry,'recovery timer must be scheduled');
      timers.delete(entry[0]);entry[1].fn();await ctx.arenaOperation;
    }};
}

test('credentials recover after startup outage without resetting rover',async()=>{
  const h=harness();h.setFailure(true);
  await h.ctx.arenaJoin();assert.equal(h.clients.length,0);
  h.setFailure(false);await h.retry();
  assert.equal(h.clients.length,1);assert.equal(h.ctx.arenaStatus().joined,true);
  assert.equal(h.ctx.arenaStatus().error,null);assert.equal(h.timers.size,0);
});
test('failed renewal retries and clears the error on recovery',async()=>{
  const h=harness();await h.ctx.arenaJoin();h.setFailure(true);
  await h.clients[0].events['token-privilege-will-expire']();
  assert.match(h.ctx.arenaStatus().error,/503/);
  h.setFailure(false);await h.retry();
  assert.equal(h.clients[0].renews,1);assert.equal(h.clients.length,1);
  assert.equal(h.ctx.arenaStatus().error,null);
});
test('expired token rejoins only the arena client',async()=>{
  const h=harness();await h.ctx.arenaJoin();
  h.clients[0].events['token-privilege-did-expire']();await h.retry();
  assert.equal(h.clients[0].leaves,1);assert.equal(h.clients[0].renews,0);
  assert.equal(h.clients.length,2);assert.equal(h.ctx.arenaStatus().joined,true);
});
test('changed identity rejoins and updates camera map',async()=>{
  const h=harness();await h.ctx.arenaJoin();
  h.setPayload({USERID:43,CAMERAS:{2:1002}});await h.ctx.arenaRenewToken();
  assert.equal(h.clients.length,2);assert.equal(h.clients[0].leaves,1);
  assert.equal(h.ctx.arenaStatus().cameras[0].cam,2);
});
test('concurrent joins share one operation',async()=>{
  const h=harness();await Promise.all([h.ctx.arenaJoin(),h.ctx.arenaJoin()]);
  assert.equal(h.clients.length,1);
});
