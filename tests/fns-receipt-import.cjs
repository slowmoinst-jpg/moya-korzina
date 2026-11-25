const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function readCabinet(denied) {
  const source = fs.readFileSync('android/app/app/src/main/assets/fns-receipts.js', 'utf8');
  let listener;
  let document;
  const requests = [];
  const start = vm.runInNewContext(source, {
    location: {origin: 'https://lkdr.nalog.ru'}, AbortSignal,
    window: {addEventListener(_, f) { listener = f; }, removeEventListener() {}},
    setTimeout: (f, ms) => setTimeout(f, ms === 1000 ? 0 : ms), clearTimeout,
    async fetch(path, options) {
      assert.equal(options.credentials, 'same-origin');
      const body = JSON.parse(options.body);
      requests.push(body.key ?? body.offset);
      if (denied) return {ok:false, status:401};
      if (path.endsWith('fiscal_data')) return body.key === 'missing' ? {ok:false,status:404} :
        {ok:true,json:async () => ({document:{receipt:{items:[{name:'Яйца',price:9000}], unrelated:'omit'}}})};
      const rows = body.offset ? [{key:'old'}] : [{key:'new'}, {key:'missing'}];
      return {ok:true,json:async () => ({receipts:rows,hasMore:body.offset === 0})};
    },
  });
  assert.equal(start('test'), true);
  let finish;
  const result = new Promise(resolve => { finish = resolve; });
  const port = {postMessage(message) {
    const m = JSON.parse(message);
    if (m.action === 'done' || m.action === 'error') { finish(m); return; }
    const reply = m.action === 'needed' ? {ok:true,needed:m.keys.filter(k => k !== 'old')} :
      {ok:true,receipts:1,skipped:0,other:0,failed:0};
    if (m.action === 'save') document = JSON.parse(m.document);
    queueMicrotask(() => port.onmessage({data:JSON.stringify(reply)}));
  }};
  await listener({data:'test',ports:[port]});
  const report = await result;
  if (denied) {
    assert.equal(report.reason, 'permission');
    assert.equal(document, undefined);
  } else {
    assert.deepEqual(requests,[0,'new','missing',2]);
    assert.deepEqual([report.imported,report.skipped,report.failed],[1,1,1]);
    assert.deepEqual(Object.keys(document.receipts[0].fiscalData),['items']);
  }
}
Promise.all([readCabinet(false), readCabinet(true)]).catch(e => { console.error(e); process.exitCode = 1; });
