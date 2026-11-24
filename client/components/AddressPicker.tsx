import React, { useEffect, useId, useRef, useState } from 'react';
import { apiUrl, reverseGeocode, suggestAddress } from '../api';
import type { AddressSuggestion, LeafletMap, LeafletMarker } from '../types';
import { Icon } from './Icons';

interface AddressPickerProps {
  address: string | null;
  point: [number, number] | null;
  disabled?: boolean;
  onSave: (address: string | null, point: [number, number] | null) => void;
}

function loadLeafletScript(): Promise<void> {
  if (typeof window === 'undefined') return Promise.resolve();
  if (!document.querySelector('link[data-leaflet-css="1"]')) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = apiUrl('/static/leaflet/leaflet.css');
    link.dataset.leafletCss = '1';
    document.head.appendChild(link);
  }
  if (window.L) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const existing = document.querySelector('script[data-leaflet="1"]') as HTMLScriptElement | null;
    if (existing) {
      existing.addEventListener('load', () => resolve(), { once: true });
      existing.addEventListener('error', () => reject(new Error('Leaflet failed')), { once: true });
      return;
    }
    const s = document.createElement('script');
    s.src = apiUrl('/static/leaflet/leaflet.js');
    s.dataset.leaflet = '1';
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error('Leaflet failed'));
    document.head.appendChild(s);
  });
}

export const AddressPicker: React.FC<AddressPickerProps> = ({
  address,
  point,
  disabled,
  onSave,
}) => {
  const [value, setValue] = useState(address || '');
  const [coords, setCoords] = useState<[number, number] | null>(point);
  const [suggestions, setSuggestions] = useState<AddressSuggestion[]>([]);
  const [loadingSuggest, setLoadingSuggest] = useState(false);
  const [searchedEmpty, setSearchedEmpty] = useState(false);
  const [searchFailed, setSearchFailed] = useState(false);
  const [showMap, setShowMap] = useState(false);
  const [mapReady, setMapReady] = useState(false);
  const [savedNotice, setSavedNotice] = useState(false);
  const [editing, setEditing] = useState(false);
  const formId = useId();
  const pickerRef = useRef<HTMLDivElement>(null);

  const mapBoxRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const markerRef = useRef<LeafletMarker | null>(null);
  const debounceRef = useRef<number | null>(null);
  const reqSeqRef = useRef<number>(0);

  useEffect(() => {
    setValue(address || '');
    setCoords(point);
  }, [address, point?.[0], point?.[1]]);

  useEffect(() => {
    if (!editing || !pickerRef.current?.closest('.spa-mobile-shell')) return;
    let frame = 0;
    const revealField = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        pickerRef.current?.closest('section')?.scrollIntoView({ block: 'start' });
      });
    };
    revealField();
    window.addEventListener('resize', revealField);
    window.visualViewport?.addEventListener('resize', revealField);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', revealField);
      window.visualViewport?.removeEventListener('resize', revealField);
    };
  }, [editing]);

  useEffect(() => {
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
    };
  }, []);

  const handleInput = (next: string) => {
    setValue(next);
    setCoords(null);
    setSuggestions([]);
    setSearchFailed(false);
    setSavedNotice(false);
    setSearchedEmpty(false);
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    const trimmed = next.trim();
    if (trimmed.length < 3) {
      reqSeqRef.current += 1;
      setSuggestions([]);
      setLoadingSuggest(false);
      return;
    }
    setLoadingSuggest(true);
    const seq = ++reqSeqRef.current;
    debounceRef.current = window.setTimeout(async () => {
      try {
        const items = await suggestAddress(trimmed);
        if (seq !== reqSeqRef.current) return;
        setSuggestions(items);
        setSearchedEmpty(items.length === 0);
      } catch {
        if (seq !== reqSeqRef.current) return;
        setSuggestions([]);
        setSearchFailed(true);
      } finally {
        if (seq === reqSeqRef.current) setLoadingSuggest(false);
      }
    }, 400);
  };

  const applySuggestion = (s: AddressSuggestion) => {
    reqSeqRef.current += 1;
    setValue(s.label);
    const pt: [number, number] = [s.lat, s.lon];
    setCoords(pt);
    setSuggestions([]);
    setLoadingSuggest(false);
    setSearchedEmpty(false);
    setSearchFailed(false);
    onSave(s.label, pt);
    setSavedNotice(true);
    if (mapRef.current && markerRef.current) {
      mapRef.current.setView(pt, 16);
      markerRef.current.setLatLng(pt);
    }
  };

  useEffect(() => {
    if (!showMap || !mapBoxRef.current) return;
    let active = true;

    loadLeafletScript()
      .then(() => {
        if (!active || !mapBoxRef.current || !window.L) return;
        const start: [number, number] = coords || [59.9356, 30.3259];
        const map = window.L.map(mapBoxRef.current, {
          zoomControl: true,
          attributionControl: false,
        });
        map.setView(start, 14);
        window.L.tileLayer(apiUrl('/api/map/tile/{z}/{x}/{y}.png'), {
          maxZoom: 18,
        }).addTo(map);

        const pinIcon = window.L.divIcon({
          className: 'spa-map-pin',
          html: '<div class="spa-pin-dot"></div>',
          iconSize: [20, 20],
          iconAnchor: [10, 10],
        });
        const marker = window.L.marker(start, { draggable: true, icon: pinIcon }).addTo(map);

        const updateFromLatLng = async (lat: number, lng: number) => {
          const seq = ++reqSeqRef.current;
          setLoadingSuggest(false);
          setSuggestions([]);
          setSearchedEmpty(false);
          setSearchFailed(false);
          const pt: [number, number] = [lat, lng];
          try {
            const addr = await reverseGeocode(lat, lng);
            if (!active || reqSeqRef.current !== seq) return;
            setCoords(pt);
            setValue(addr);
            onSave(addr, pt);
            setSavedNotice(true);
          } catch {
            // keep coordinates
          }
        };

        map.on('click', (e) => {
          marker.setLatLng([e.latlng.lat, e.latlng.lng]);
          void updateFromLatLng(e.latlng.lat, e.latlng.lng);
        });

        marker.on('dragend', () => {
          const ll = marker.getLatLng();
          void updateFromLatLng(ll.lat, ll.lng);
        });

        mapRef.current = map;
        markerRef.current = marker;
        setMapReady(true);
        window.setTimeout(() => map.invalidateSize(), 120);
      })
      .catch(() => {
        setMapReady(false);
      });

    return () => {
      active = false;
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
        markerRef.current = null;
      }
    };
  }, [showMap]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    reqSeqRef.current += 1;
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    setLoadingSuggest(false);
    setSearchFailed(false);
    const cleaned = value.trim();
    onSave(cleaned || null, cleaned ? coords : null);
    setSuggestions([]);
    setSearchedEmpty(false);
    setSavedNotice(true);
  };

  return (
    <div className="spa-addr" ref={pickerRef}>
      <form className="addr" id={formId} onSubmit={handleSubmit}>
        <div className="spa-addr-field">
          <input
            type="text"
            value={value}
            onFocus={() => setEditing(true)}
            onBlur={() => setEditing(false)}
            onChange={(e) => handleInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                reqSeqRef.current += 1;
                if (debounceRef.current) window.clearTimeout(debounceRef.current);
                setLoadingSuggest(false);
                setSearchFailed(false);
                setSuggestions([]);
                setSearchedEmpty(false);
              }
            }}
            placeholder="Город, улица, дом"
            aria-label="Адрес доставки"
            autoComplete="off"
            disabled={disabled}
          />
          {value && !disabled && (
            <button
              type="button"
              className="spa-addr-clear"
              aria-label="Очистить адрес"
              onClick={() => handleInput('')}
            >
              <Icon name="x" />
            </button>
          )}
        </div>
      </form>

      {loadingSuggest && (
        <div className="spa-suggest-status">Ищем подходящие адреса…</div>
      )}

      {!loadingSuggest && suggestions.length > 0 && (
        <ul className="spa-suggest" role="listbox" aria-label="Подсказки адресов">
          {suggestions.map((s, idx) => (
            <li key={`${s.lat}-${s.lon}-${idx}`}>
              <button type="button" onClick={() => applySuggestion(s)}>
                <Icon name="pin" />
                <span className="spa-suggest-txt">
                  <strong>{s.label}</strong>
                  {s.full && s.full !== s.label && <small>{s.full}</small>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {!loadingSuggest && searchedEmpty && value.trim().length >= 3 && (
        <div className="spa-suggest-status">
          Адрес не найден. Уточните улицу и дом или укажите точку на карте.
        </div>
      )}
      {searchFailed && <div className="spa-suggest-status">Поиск адресов временно недоступен. Можно сохранить адрес вручную.</div>}

      <div className="spa-addr-actions">
        <button type="submit" form={formId} className="bt sm lime" disabled={disabled}>
          Сохранить
        </button>
        <button
          type="button"
          className="bt sm"
          onClick={() => setShowMap((v) => !v)}
        >
          <Icon name="pin" />
          <span>{showMap ? 'Скрыть карту' : 'Указать на карте'}</span>
        </button>
        {savedNotice && <span className="spa-saved">Адрес сохранён</span>}
      </div>

      {showMap && (
        <div className="spa-map-wrap">
          <div ref={mapBoxRef} className="spa-map-canvas" />
          {!mapReady && <div className="spa-map-hint">Загружаем карту…</div>}
          <div className="spa-map-cap">Нажмите на дом или перетащите точку</div>
        </div>
      )}
    </div>
  );
};
