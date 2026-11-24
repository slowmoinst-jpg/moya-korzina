import { useState } from 'react';
import { KorzinaBridge } from '../device';
import type { ApkUpdate } from '../types';

export function ApkUpdateBanner({ update }: { update: ApkUpdate }) {
  const [failed, setFailed] = useState(false);
  const inApp = KorzinaBridge.supportsInAppUpdate();
  return (
    <div className="note warn apk-update" role="status">
      <span>{failed ? 'Не удалось открыть установщик' : `Вышла версия ${update.version} приложения`}</span>
      {inApp ? (
        <button type="button" className="bt sm" onClick={() => setFailed(!KorzinaBridge.updateApp())}>
          Обновить
        </button>
      ) : update.canDownload ? (
        <a className="bt sm" href={update.url}>Скачать</a>
      ) : (
        <span className="apk-update-url">Откройте в браузере телефона: {update.url}</span>
      )}
    </div>
  );
}
