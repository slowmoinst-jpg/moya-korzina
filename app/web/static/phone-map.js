/* Карта и подсказки адреса на телефонном экране настроек.

   ЧТО ЗДЕСЬ ЕСТЬ. Поле адреса подсказывает после трёх букв (задержка 500 мс, чтобы
   набор не слал запрос на каждую букву), «Найти» ищет сразу и вдобавок спрашивает
   Nominatim, а карта Leaflet показывает точку и умеет ставить и перетаскивать пин.
   Пин разворачивается в адрес обратным геокодом сервера (/phone/address/reverse).

   ЧЕГО ЗДЕСЬ НЕТ НАРОЧНО. Поле не дописывается само: строка встаёт только после
   касания подсказки. Молча подставленный адрес человек не заметит и уедет по
   чужому — это не удобство, а подмена.

   БЕЗ БИБЛИОТЕК. Leaflet лежит рядом (static/leaflet/), тайлы идут через свой
   сервер (/phone/map/tile/...): карта не должна зависеть от чужого CDN, которого
   у телефона в приложении может не быть вовсе. */
(function () {
  'use strict';

  var form = document.getElementById('addr-form');
  var mapBox = document.getElementById('addr-map');
  if (!form || !mapBox) return;

  var input = document.getElementById('addr-input');
  var list = document.getElementById('addr-list');
  var pointInput = document.getElementById('addr-point');
  var findButton = document.getElementById('addr-find');
  var showButton = document.getElementById('addr-map-show');
  var hint = document.getElementById('addr-map-hint');

  var suggestUrl = mapBox.getAttribute('data-suggest-url') || '';
  var reverseUrl = mapBox.getAttribute('data-reverse-url') || '';
  var tileBase = mapBox.getAttribute('data-tile-base') || '';
  var startLat = parseFloat(mapBox.getAttribute('data-lat'));
  var startLon = parseFloat(mapBox.getAttribute('data-lon'));

  var DEBOUNCE_MS = 500;
  // Москва: центр по умолчанию для карты без сохранённой точки. Сервер сюда не
  // ходит за координатами адреса — открытие настроек не ждёт Nominatim.
  var DEFAULT_CENTER = [55.7558, 37.6176];
  var timer = null;
  var suggestSeq = 0;
  var reverseSeq = 0;
  var map = null;
  var marker = null;
  var tilesWarned = false;

  // ---------- подсказки ----------
  function hideList() {
    if (!list) return;
    list.hidden = true;
    list.textContent = '';
  }

  function showHint(text) {
    if (!hint) return;
    hint.hidden = false;
    hint.textContent = text;
  }

  function setPoint(latitude, longitude) {
    if (!pointInput) return;
    if (latitude == null || longitude == null) {
      pointInput.value = '';
    } else {
      pointInput.value = Number(latitude).toFixed(6) + ',' + Number(longitude).toFixed(6);
    }
  }

  function dropMarker() {
    if (map && marker) {
      map.removeLayer(marker);
    }
    marker = null;
    setPoint(null, null);
  }

  function render(items) {
    list.textContent = '';
    if (!items.length) {
      var empty = document.createElement('div');
      empty.className = 'hint';
      empty.textContent = 'Ничего не нашлось — допишите адрес или поставьте пин на карте.';
      list.appendChild(empty);
      list.hidden = false;
      return;
    }
    items.forEach(function (value) {
      var button = document.createElement('button');
      button.type = 'button';
      button.setAttribute('role', 'option');
      button.textContent = value;
      button.addEventListener('click', function () {
        // Только по выбору: человек увидел строку и коснулся её.
        input.value = value;
        hideList();
        // Адрес новый — старый пин к нему уже не относится.
        dropMarker();
      });
      list.appendChild(button);
    });
    list.hidden = false;
  }

  function ask(force) {
    var query = (input.value || '').trim();
    if (query.length < 3) {
      hideList();
      return;
    }
    var seq = ++suggestSeq;
    var url = suggestUrl + '?q=' + encodeURIComponent(query) + (force ? '&find=1' : '');
    fetch(url, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'})
      .then(function (answer) {
        return answer.ok ? answer.json() : {items: []};
      })
      .then(function (data) {
        if (seq !== suggestSeq) return;                    // ответ на устаревший набор
        if ((input.value || '').trim() !== query) return;
        render((data && data.items) || []);
      })
      .catch(function () {
        if (seq === suggestSeq) hideList();                // подсказка — помощь, не обязанность
      });
  }

  if (input && list) {
    input.addEventListener('input', function () {
      // Человек правит адрес руками — точка с карты перестаёт ему соответствовать.
      dropMarker();
      clearTimeout(timer);
      timer = setTimeout(function () { ask(false); }, DEBOUNCE_MS);
    });
    input.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') hideList();
      if (event.key === 'Enter') {
        // «Перейти» на клавиатуре телефона — это поиск, а не сохранение:
        // сохранить человек всегда успеет кнопкой, а искать хочет сразу.
        event.preventDefault();
        clearTimeout(timer);
        ask(true);
      }
    });
    if (findButton) {
      findButton.addEventListener('click', function (event) {
        event.preventDefault();
        clearTimeout(timer);
        ask(true);                                          // здесь можно Nominatim
      });
    }
    document.addEventListener('click', function (event) {
      if (!list.hidden && event.target !== input && !list.contains(event.target)) {
        hideList();
      }
    });
  }

  // ---------- карта ----------
  function onMarkerPlaced(event) {
    var position = event.target.getLatLng();
    place(position.lat, position.lng, true);
  }

  function place(latitude, longitude, lookup) {
    if (!map) return;
    if (!marker) {
      marker = L.marker([latitude, longitude], {draggable: true}).addTo(map);
      marker.on('dragend', onMarkerPlaced);
    } else {
      marker.setLatLng([latitude, longitude]);
    }
    setPoint(latitude, longitude);
    if (lookup) lookupAddress(latitude, longitude);
  }

  function lookupAddress(latitude, longitude) {
    if (!reverseUrl) return;
    var seq = ++reverseSeq;
    showHint('Ищем адрес этой точки…');
    var url = reverseUrl + '?lat=' + Number(latitude).toFixed(6)
            + '&lon=' + Number(longitude).toFixed(6);
    fetch(url, {headers: {'Accept': 'application/json'}, credentials: 'same-origin'})
      .then(function (answer) {
        return answer.ok ? answer.json() : {address: null};
      })
      .then(function (data) {
        if (seq !== reverseSeq) return;                     // пин уже переставили
        var found = data && data.address;
        if (found) {
          input.value = found;
          hideList();
          showHint('Адрес подставлен с карты. Проверьте его и нажмите «Сохранить».');
        } else {
          // Поле НЕ трогаем: «Москва» вместо дома — это подмена, а не подсказка.
          showHint('Точка поставлена, адрес не разобран — проверьте его в поле выше.');
        }
      })
      .catch(function () {
        if (seq === reverseSeq) {
          showHint('Точка поставлена, адрес не разобран — проверьте его в поле выше.');
        }
      });
  }

  function onTileError() {
    // 429 от сервера Leaflet показывает серым квадратом; человеку нужны слова.
    if (tilesWarned) return;
    tilesWarned = true;
    showHint('Карта ненадолго отдыхает — подождите немного и попробуйте снова.');
  }

  function initMap() {
    if (map || !window.L || !tileBase) return;
    map = L.map(mapBox, {minZoom: 10, maxZoom: 18});
    var tiles = L.tileLayer(tileBase + '/{z}/{x}/{y}.png', {
      minZoom: 10,
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    });
    tiles.on('tileerror', onTileError);
    tiles.addTo(map);
    if (isFinite(startLat) && isFinite(startLon)) {
      map.setView([startLat, startLon], 16);
      marker = L.marker([startLat, startLon], {draggable: true}).addTo(map);
      marker.on('dragend', onMarkerPlaced);
    } else {
      map.setView(DEFAULT_CENTER, 12);
    }
    map.on('click', function (event) {
      place(event.latlng.lat, event.latlng.lng, true);
    });
  }

  function openMap() {
    mapBox.hidden = false;
    if (!window.L) {
      showHint('Карта не загрузилась — адрес можно ввести руками.');
      return;
    }
    initMap();
    if (map && map.invalidateSize) {
      setTimeout(function () { map.invalidateSize(); }, 60);
    }
  }

  if (showButton) {
    showButton.addEventListener('click', function (event) {
      if (!window.L) return;              // без Leaflet пусть сработает ссылка ?map=1
      event.preventDefault();
      openMap();
    });
  }

  function start() {
    if (!mapBox.hidden) openMap();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
