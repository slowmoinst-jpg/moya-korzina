// Reads the cards and receipt dialog shown to the purchaser in the official cabinet.
(async function readCabinetScreen(save, progress, active, resume = {}) {
  const label = el => (el?.textContent || '').trim();
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function waitFor(find, timeout = 14000) {
    const end = Date.now() + timeout;
    do {
      if (!active()) throw Error('cancelled');
      const value = find();
      if (value) return value;
      await sleep(200);
    } while (Date.now() < end);
    throw Error('screen');
  }
  function leaves(root) {
    return [...root.querySelectorAll('div,span,p,button')].filter(el => !el.childElementCount);
  }
  const number = text => /^\d+(?:[.,]\d+)?$/.test(text.replace(/\s/g, ''))
    ? Number(text.replace(/\s/g, '').replace(',', '.')) : NaN;
  function readDialog(root, brand) {
    const cells = leaves(root);
    const value = name => label(cells.find(el => label(el) === name)?.nextElementSibling);
    const headings = cells.find(el => label(el) === 'Предмет расчета')?.parentElement;
    const fn = value('ФН:'), fd = value('ФД №:'), sign = value('ФП:');
    const date = value('Дата/Время');
    const total = number(value('Итог:'));
    if (![fn, fd, sign].every(v => /^\d+$/.test(v)) || !/^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$/.test(date)
        || !Number.isFinite(total) || !headings?.nextElementSibling) return null;
    const items = [];
    for (const wrapper of headings.nextElementSibling.children) {
      const row = wrapper.firstElementChild;
      if (!row || row.children.length !== 4) return null;
      const [subject, price, qty, sum] = [...row.children];
      const name = label(subject.lastElementChild);
      const quantity = number(label(qty)), unitPrice = number(label(price)), amount = number(label(sum));
      if (!name || ![quantity, unitPrice, amount].every(Number.isFinite) || quantity <= 0) return null;
      items.push({name, quantity, price:Math.round(unitPrice * 100), sum:Math.round(amount * 100)});
    }
    if (!items.length) return null;
    const seller = brand || value('Место расчетов:') || label(root.children[2]);
    return {key:'fiscal:' + fn + ':' + fd + ':' + sign, date, store:seller,
      total:total.toFixed(2), fiscalData:{dateTime:date, retailPlace:seller, items, totalSum:Math.round(total * 100)}};
  }
  function cards() {
    // The cabinet can show a receipt before a seller's tax ID is known.
    return leaves(document).filter(el => /^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$/.test(label(el)))
      .map(el => el.parentElement.parentElement)
      .filter(card => card.getClientRects().length && /ИНН/.test(label(card.lastElementChild)));
  }
  function moreButton() {
    return [...document.querySelectorAll('button,[role=button],a')].find(el =>
      /^(?:загрузить|показать) ещ[её]$/i.test(label(el)) && el.getClientRects().length);
  }
  const open = () => document.getElementById('receipt-container');
  let failed = 0;
  // The public card identifies the head even when its receipt details are incomplete.
  function cardMark(card) {
    let hash = 0;
    for (const ch of label(card)) hash = (Math.imul(31, hash) + ch.charCodeAt(0)) | 0;
    return 'card:' + hash;
  }
  async function readOpen(brand) {
    const entry = await waitFor(() => {
      const root = open();
      if (!root) return null;
      if (label(root).includes('Ошибка при получении данных чека')) throw Error('unreadable');
      return readDialog(root, brand);
    });
    const result = await save(entry);
    failed += result.failed;
    return result.failed ? null : entry.key;
  }
  async function closeDialog() {
    if (!open()) return;
    const close = document.getElementById('close-icon');
    if (!close) throw Error('screen');
    close.click();
    await waitFor(() => !open());
  }
  // A restored receipt dialog proves there is one document, not that the list is exhausted.
  if (open()) { await readOpen(''); await closeDialog(); }
  let offset = 0, head = '', verifyAt = -1, previousFailures = 0;
  const checkpoint = resume.checkpoint || (async () => {});
  let emptySince = 0;
  let list = await waitFor(() => {
    const rows = cards();
    if (rows.length) return rows;
    const empty = leaves(document).some(el => label(el) === 'Нет данных' && el.getClientRects().length);
    if (!empty) emptySince = 0;
    else if (!emptySince) emptySince = Date.now();
    return empty && Date.now() - emptySince > 2500 ? [] : null;
  });
  async function loadNext(button) {
    const before = list.length;
    await sleep(1000);
    if (!active()) throw Error('cancelled');
    button.click();
    // During a slow request the button can disappear before the new rows arrive.
    list = await waitFor(() => {
      const rows = cards();
      return rows.length > before ? rows : null;
    });
  }
  let lastHadMore = false;
  while (active()) {
    const next = moreButton();
    if (offset >= list.length) {
      if (next) {
        if (verifyAt > 0) await progress(verifyAt, list.length, false, 'resume');
        await loadNext(next); continue;
      }
      if (verifyAt > list.length) {
        offset = 1; verifyAt = -1; previousFailures = 0; resume = {};
        continue;
      }
      // Closing a dialog may temporarily hide or replace the list and its pagination button.
      // Require the same rendered tail after it settles. Rows appended by the cabinet may already be present.
      const tail = list[offset - 1];
      let settledAt = 0;
      const boundary = await waitFor(() => {
        const current = cards(), more = moreButton();
        if (current.length > offset || more) return {current, more};
        if (current.length !== offset || (tail && cardMark(current[offset - 1]) !== cardMark(tail))
            || (lastHadMore && tail)) { settledAt = 0; return null; }
        if (!settledAt) settledAt = Date.now();
        return Date.now() - settledAt >= 1800 ? {current, end:true} : null;
      });
      list = boundary.current;
      if (!boundary.end) {
        if (boundary.more && list.length === offset) await loadNext(boundary.more);
        continue;
      }
      const gaps = failed + previousFailures;
      // The next run revisits gaps, but a bad receipt never keeps later pages out of the archive.
      await progress(offset, offset, true, 'end');
      await checkpoint(gaps ? {} : {head, total:offset, complete:true});
      return gaps;
    }
    if (!list[offset].isConnected) { list = cards(); continue; }
    const card = list[offset++];
    lastHadMore = Boolean(next);
    const brand = label(card.children[1]?.lastElementChild?.firstElementChild);
    await progress(offset, list.length, !next, verifyAt > 0 || (offset === 1 && resume.offset > 2) ? 'resume' : 'read');
    await sleep(1000);
    if (!active()) throw Error('cancelled');
    card.click();
    let key = null;
    try { key = await readOpen(brand); }
    catch (e) {
      if (!['screen','unreadable'].includes(e.message)) throw e;
      failed++;
    }
    await closeDialog();
    const marker = key || cardMark(card);
    if (offset === 1) head = marker;
    if (verifyAt === offset) {
      if (marker !== resume.last) {
        offset = 1; verifyAt = -1; previousFailures = 0; resume = {};
        continue;
      }
      previousFailures = Number(resume.gaps) || 0;
      verifyAt = -1;
    }
    if ((key && key === resume.head) || (offset === 1 && head === resume.head)) {
      if (resume.complete && resume.total > 0 && !failed) {
        const total = resume.total + offset - 1;
        await progress(total, total, true, 'end');
        await checkpoint({head, total, complete:true});
        return failed;
      }
      if (resume.offset > 2) {
        verifyAt = offset + resume.offset - 1;
        offset = verifyAt - 1;
        continue;
      }
    }
    await checkpoint({head, last:marker, offset, gaps:failed + previousFailures, complete:false});
    // Discover additional pages gradually, so the first receipts can already be saved.
    if (offset % 5 === 0 && next && next.isConnected) await loadNext(next);
  }
  throw Error('cancelled');
})
