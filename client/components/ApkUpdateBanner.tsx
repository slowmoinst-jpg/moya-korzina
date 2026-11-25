import type { ApkUpdate } from '../types';

export function ApkUpdateBanner({ update }: { update: ApkUpdate }) {
  return (
    <div className="note warn apk-update" role="status">
      <span>Вышла версия {update.version} приложения</span>
      {update.canDownload ? (
        <a className="bt sm" href={update.url}>Скачать APK</a>
      ) : (
        <span className="apk-update-url">Откройте в браузере телефона: {update.url}</span>
      )}
    </div>
  );
}
