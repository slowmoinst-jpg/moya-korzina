from pathlib import Path
import base64
import pytest

from app import config, users
from app.web import create_app

ASSETS = Path(__file__).resolve().parents[1] / "android/app/app/src/main/assets"
CABINET = """
<div id="list"></div><button id="more">Загрузить еще</button>
<script>
const list = document.querySelector('#list');
function addCard(seller, fd) {
  const el = document.createElement('div');
  el.innerHTML = '<div><div>23.11.2026 20:06</div><input type="checkbox"></div>'
    + '<div><div></div><div><div>'+seller+'</div><div>Юрлицо</div></div></div>'
    + '<div><div>274,00 ₽</div><div>ИНН 7704217370</div></div>';
  el.onclick = () => {
    const d = document.createElement('div'); d.id = 'receipt-container';
    d.innerHTML = '<div>КАССОВЫЙ ЧЕК<button id="close-icon">×</button></div><div>Приход</div><div>Юрлицо</div>'
      + '<div><div><div>Предмет расчета</div><div>Цена, ₽</div><div>Кол-во</div><div>Сумма, ₽</div></div>'
      + '<div><div><div><div><div>1.</div><div><div>Молоко 950 мл</div></div></div>'
      + '<div>137,00</div><div>2</div><div>274,00</div></div></div></div></div>'
      + [['Итог:','274,00'],['Дата/Время','23.11.2026 20:06'],['ФН:','1234567890123456'],
         ['ФД №:',fd],['ФП:','1020123456']].map(([k,v])=>'<div><div>'+k+'</div><div>'+v+'</div></div>').join('');
    document.body.append(d); d.querySelector('button').onclick = () => d.remove();
  };
  list.append(el);
}
addCard('Озон', '311');
document.querySelector('#more').onclick = e => {addCard('Магнит','312'); e.target.remove();};
</script>
"""

def test_visible_cabinet_after_denial_and_repeat(tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    monkeypatch.setattr(config, "ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "data/users"))
    monkeypatch.setattr(users, "PIN_FILE", str(tmp_path / "install.pin"))
    client = create_app().test_client()
    assert client.post("/api/auth/login", json={"phone":"79995550172"}).status_code == 200
    def save(document):
        encoded = base64.b64encode(document.encode()).decode()
        response = client.post("/api/receipts/file", json={"content":encoded})
        assert response.status_code == 200
        return response.get_json()
    with pw.sync_playwright() as engine:
        try:
            browser = engine.chromium.launch(channel="chrome")
        except pw.Error as exc:
            pytest.skip(str(exc))
        page = browser.new_page(viewport={"width":360,"height":800})
        page.route("https://lkdr.nalog.ru/**", lambda route:
                   route.fulfill(status=403) if "/api/" in route.request.url
                   else route.fulfill(body=CABINET, content_type="text/html; charset=utf-8"))
        page.expose_function("saveReceipt", save)
        source = "(nonce => {const readCabinetScreen=" + (ASSETS/"fns-screen.js").read_text(encoding="utf8")
        source += ";return (" + (ASSETS/"fns-receipts.js").read_text(encoding="utf8") + ")(nonce)})"
        for expected in [(1,1,0),(0,0,2)]:
            page.goto("https://lkdr.nalog.ru/")
            assert page.evaluate(source, "test")
            result = page.evaluate("""() => new Promise(resolve => {
              const p = new MessageChannel();
              p.port1.onmessage = async e => {
                const msg = JSON.parse(e.data);
                if (['done','error'].includes(msg.action)) {resolve(msg);return;}
                const reply = msg.action === 'save' ? await saveReceipt(msg.document) : {ok:true};
                p.port1.postMessage(JSON.stringify(reply));
              };
              window.postMessage('test',location.origin,[p.port2]);
            })""")
            assert (result["imported"],result["other"],result["skipped"]) == expected
            assert result["failed"] == 0
        browser.close()
    snapshot = client.get("/api/sync").get_json()["snapshot"]
    assert snapshot["otherReceipts"]["recent"][0]["store"] == "Озон"
    assert snapshot["receipts"][0]["total"] == 274
    assert snapshot["history"][0]["unit_price"] == 137


def test_automatic_reader_resumes_and_detects_new_receipts():
    pw = pytest.importorskip("playwright.sync_api")
    reader = (ASSETS / "fns-screen.js").read_text(encoding="utf8")
    with pw.sync_playwright() as engine:
        try:
            browser = engine.chromium.launch(channel="chrome")
        except pw.Error as exc:
            pytest.skip(str(exc))
        page = browser.new_page()
        page.set_content(CABINET)
        page.evaluate("document.querySelector('#more').remove()")
        for fd in ('312', '313', '314'):
            page.evaluate("fd => addCard('Магнит', fd)", fd)
        run = """async ({state, limit}) => {
          const keys = [];
          let cursor = state;
          try {
            await (READER)(async entry => {
              keys.push(entry.key.split(':')[2]);
              return {failed: 0};
            }, async () => { if (keys.length >= limit) throw Error('cancelled'); }, () => true, {
              ...state, checkpoint: async next => { cursor = next; }
            });
          } catch (e) { if (e.message !== 'cancelled') throw e; }
          return {keys, cursor};
        }""".replace("READER", reader)
        initial = page.evaluate(run, {"state": {}, "limit": 3})
        assert initial["keys"] == ["311", "312", "313"]
        resumed = page.evaluate(run, {"state": initial["cursor"], "limit": 10})
        assert resumed["keys"] == ["311", "313", "314"]
        assert resumed["cursor"]["complete"]
        page.evaluate("() => {addCard('Магнит','315'); list.prepend(list.lastElementChild);}")
        incremental = page.evaluate(run, {"state": resumed["cursor"], "limit": 10})
        assert incremental["keys"] == ["315", "311"]
        assert incremental["cursor"]["complete"]
        browser.close()

def test_large_cabinet_continues_past_bad_receipt_and_resumes():
    pw = pytest.importorskip("playwright.sync_api")
    # Compress only inter-request pacing for this offline, 2,100-document fixture.
    reader = (ASSETS / "fns-screen.js").read_text(encoding="utf8").replace("await sleep(1000)", "await Promise.resolve()")
    fixture = CABINET[:CABINET.index("addCard('Озон'")] + """
      let next = 1;
      const more = document.querySelector('#more');
      more.textContent = 'Показать ещё';
      function page() {
        const end = Math.min(2101, next + 100);
        while (next < end) addCard('Озон', String(next++));
        if (next === 2101) more.remove(); else more.hidden = false;
      }
      more.onclick = () => {more.hidden = true; setTimeout(page, 5);};
      page();
      if (!window.repaired) list.children[3].onclick = () => {
        list.children[0].click();
        const dialog = document.querySelector('#receipt-container');
        dialog.prepend('Ошибка при получении данных чека');
      };
      </script>"""
    run = """async ({cursor, stop}) => {
      let state = cursor, last = 0, total = 0;
      const keys = [];
      let failed = null;
      try {
        failed = await (READER)(async entry => {
          const id = Number(entry.key.split(':')[2]);keys.push(id);return {failed:0};
        }, async (index, loaded, end) => {
          last = index;
          if (end) total = loaded;
          if (index === stop) throw Error('test stop');
        }, () => true, {...cursor, checkpoint: async value => {state=value;}});
      } catch (e) {if (e.message !== 'test stop') throw e;}
      return {state, keys, last, total, failed};
    }""".replace("READER", reader)
    with pw.sync_playwright() as engine:
        try:
            browser = engine.chromium.launch(channel="chrome")
        except pw.Error as exc:
            pytest.skip(str(exc))
        page = browser.new_page(viewport={"width": 360, "height": 800})
        page.set_content(fixture)
        first = page.evaluate(run, {"cursor": {}, "stop": 157})
        assert first["state"]["offset"] == 156 and first["state"]["gaps"] == 1
        page.goto('about:blank')
        page.set_content(fixture)
        second = page.evaluate(run, {"cursor": first["state"], "stop": -1})
        assert second["keys"][:3] == [1, 156, 157]
        assert second["last"] == second["total"] == 2100
        assert second["failed"] == 1 and second["state"] == {}
        assert set(first["keys"] + second["keys"]) == set(range(1, 2101)) - {4}
        page.goto('about:blank')
        page.evaluate("window.repaired = true")
        page.set_content(fixture)
        repaired = page.evaluate(run, {"cursor": second["state"], "stop": 6})
        assert 4 in repaired["keys"] and repaired["state"]["gaps"] == 0
        browser.close()

def test_device_progress_is_scoped_to_signed_in_account(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "data/users"))
    monkeypatch.setattr(users, "PIN_FILE", str(tmp_path / "install.pin"))
    app = create_app()
    reader = app.test_client()
    path = "/api/fns/device-progress"
    frame = {"phase": "running", "position": 6, "total": 32}
    assert reader.post(path, json=frame).status_code == 401
    reader.post("/api/auth/login", json={"phone": "79995558101"})
    assert reader.post(path, json={**frame, "position": -1}).status_code == 400
    assert reader.post(path, json=frame).status_code == 200
    stored = reader.get("/api/fns/status").get_json()["device"]
    assert stored["receivedAt"] and all(stored[k] == v for k, v in frame.items())
    second = app.test_client()
    second.post("/api/auth/login", json={"phone": "79995558102"})
    assert second.get("/api/fns/status").get_json()["device"] is None

def test_temporary_end_after_receipt_41_does_not_finish_archive():
    pw = pytest.importorskip("playwright.sync_api")
    reader = (ASSETS / "fns-screen.js").read_text(encoding="utf8").replace("await sleep(1000)", "await Promise.resolve()")
    fixture = CABINET[:CABINET.index("addCard('Озон'")] + """
      document.querySelector('#more').remove();
      for (let i = 1; i <= 41; i++) addCard('Озон',String(i));
      list.addEventListener('click', e => {
        const i = [...list.children].findIndex(el => el.contains(e.target)) + 1;
        if (i === 40) {
          const more = document.createElement('button');
          more.id = 'more'; more.textContent = 'Показать ещё';document.body.append(more);
        }
        if (i === 41) {
          document.querySelector('#more').remove();
          setTimeout(() => {for (let n = 42; n <= 54; n++) addCard('Озон',String(n));}, 500);
        }
      });
      </script>"""
    with pw.sync_playwright() as engine:
        try:
            browser = engine.chromium.launch(channel="chrome")
        except pw.Error as exc:
            pytest.skip(str(exc))
        page = browser.new_page()
        page.set_content(fixture)
        result = page.evaluate("""async () => {
          const keys = [], progress = []; let cursor;
          await (READER)(async entry => {keys.push(Number(entry.key.split(':')[2])); return {failed:0};},
            (n,total,end) => progress.push([n,total,end]), () => true, {
              head:'fiscal:1234567890123456:1:1020123456',complete:true,
              checkpoint: async next => {cursor=next}
            });
          return {keys,cursor,last:progress.at(-1)};
        }""".replace("READER", reader))
        assert result["keys"] == list(range(1, 55))
        assert result["cursor"]["total"] == 54 and result["cursor"]["complete"]
        assert result["last"] == [54,54,True]
        browser.close()
