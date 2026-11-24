// Runs after the purchaser taps «Загрузить чеки» in the official cabinet.
// Only fiscal document fields leave the page. The cabinet owns its browser session.
(function startReceiptImport(nonce) {
  if (location.origin !== 'https://lkdr.nalog.ru') return false;
  const fields = ('source receipts key date store total fiscalData ticket document receipt fiscalDocument ' +
    'dateTime retailPlace retailPlaceAddress user userInn totalSum items name quantity price sum ' +
    'paymentType paymentMethodType fiscalDriveNumber fiscalDocumentNumber fiscalSign').split(' ');
  async function run(event) {
    if (event.data !== nonce || !event.ports[0]) return;
    window.removeEventListener('message', run);
    const port = event.ports[0];
    const seen = new Set();
    let imported = 0, skipped = 0, failed = 0, other = 0;
    function receiptNode(node) {
      if (!node || typeof node !== 'object') return null;
      if (Array.isArray(node.items) && node.items.length) return node;
      for (const child of Object.values(node)) {
        const receipt = receiptNode(child);
        if (receipt) return receipt;
      }
      return null;
    }
    function exchange(payload) {
      return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(Error('timeout')), 60000);
        port.onmessage = e => {
          clearTimeout(timeout);
          const reply = JSON.parse(e.data);
          if (reply.ok) resolve(reply); else reject(Error('destination'));
        };
        port.postMessage(JSON.stringify(payload));
      });
    }
    async function read(path, body) {
      const response = await fetch(path, {method: 'POST', credentials: 'same-origin',
        redirect: 'error', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
        signal: AbortSignal.timeout(20000)});
      if (path.endsWith('/fiscal_data') && [404,410,422].includes(response.status)) return null;
      if (!response.ok) throw Error([401,403].includes(response.status) ? 'permission' : 'source');
      return response.json();
    }
    const pause = () => new Promise(resolve => setTimeout(resolve, 1000));
    try {
      for (let offset = 0; ; ) {
        const list = await read('/api/v1/receipt', {limit: 40, offset, orderBy:'CREATED_DATE:DESC'});
        if (!Array.isArray(list.receipts)) throw Error('source');
        const fresh = list.receipts.filter(r => typeof r.key === 'string' && !seen.has(r.key) && seen.add(r.key));
        if (!fresh.length) break;
        const brands = new Map((list.brands || []).map(b => [b.id,b.name]));
        const needed = new Set((await exchange({action:'needed', keys:fresh.map(r => r.key)})).needed);
        skipped += fresh.length - needed.size;
        for (const row of fresh.filter(r => needed.has(r.key))) {
          await pause();
          const fiscalData = receiptNode(await read('/api/v1/receipt/fiscal_data', {key:row.key}));
          if (!fiscalData) { failed++; continue; }
          const entry = {key:row.key, date:row.createdDate || row.receiveDate || row.buyDate,
            store:row.brand || brands.get(row.brandId) || row.retailPlace, total:row.totalSum, fiscalData};
          const text = JSON.stringify({source:'lkdr',receipts:[entry]}, fields);
          if (!text.includes('"items":[') || text.length > 170000) { failed++; continue; }
          const result = await exchange({action:'save', document:text});
          imported += result.receipts; skipped += result.skipped; other += result.other; failed += result.failed;
        }
        offset += list.receipts.length;
        if (!list.hasMore) break;
        await pause();
      }
      port.postMessage(JSON.stringify({action:'done', imported, skipped, failed, other}));
    } catch (e) {
      if (e.message === 'permission' && typeof readCabinetScreen === 'function') {
        try {
          failed += await readCabinetScreen(async entry => {
            const document = JSON.stringify({source:'lkdr',receipts:[entry]});
            if (document.length > 170000) return {receipts:0, skipped:0, other:0, failed:1};
            const saved = await exchange({action:'save', document});
            imported += saved.receipts; skipped += saved.skipped; other += saved.other;
            return saved;
          },
            index => exchange({action:'reading', index}), () => location.origin === 'https://lkdr.nalog.ru');
          port.postMessage(JSON.stringify({action:'done', imported, skipped, other, failed}));
          return;
        } catch (screenError) { e = screenError; }
      }
      port.postMessage(JSON.stringify({action:'error', reason:e.message === 'permission' ? 'permission' : 'read', imported}));
    }
  }
  window.addEventListener('message', run);
  return true;
})
