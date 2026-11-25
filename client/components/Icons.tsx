import React, { useState } from 'react';
import { chainLetter, NNBSP, rub, wholeNumberStr } from '../../src/shared/format';

export type IconName =
  | 'cart'
  | 'gear'
  | 'clock'
  | 'back'
  | 'next'
  | 'down'
  | 'check'
  | 'x'
  | 'phone'
  | 'pin'
  | 'receipt'
  | 'out'
  | 'minus'
  | 'plus'
  | 'trash'
  | 'bag'
  | 'truck'
  | 'alert'
  | 'grid'
  | 'list'
  | 'table'
  | 'more'
  | 'search';

export const Icon: React.FC<{ name: IconName; className?: string }> = ({ name, className = 'i' }) => {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden="true" data-icon={name}>
      {name === 'cart' && (
        <>
          <circle cx="9" cy="20" r="1.4" />
          <circle cx="18" cy="20" r="1.4" />
          <path d="M2.5 3.5h2.6l2.4 11.2a1.6 1.6 0 0 0 1.6 1.3h8.7a1.6 1.6 0 0 0 1.6-1.2l1.6-6.8H6.2" />
        </>
      )}
      {name === 'gear' && (
        <>
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
        </>
      )}
      {name === 'clock' && (
        <>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v5l3 2" />
        </>
      )}
      {name === 'back' && <path d="M15 6l-6 6 6 6" />}
      {name === 'next' && <path d="M9 6l6 6-6 6" />}
      {name === 'down' && <path d="M6 9l6 6 6-6" />}
      {name === 'check' && <path d="M4 12.5l5 5L20 6.5" />}
      {name === 'x' && <path d="M6 6l12 12M18 6 6 18" />}
      {name === 'phone' && (
        <>
          <rect x="7" y="2.5" width="10" height="19" rx="2" />
          <path d="M11 18.5h2" />
        </>
      )}
      {name === 'pin' && (
        <>
          <path d="M12 21s7-6.2 7-12a7 7 0 0 0-14 0c0 5.8 7 12 7 12z" />
          <circle cx="12" cy="9" r="2.5" />
        </>
      )}
      {name === 'receipt' && (
        <>
          <path d="M6 3h12v18l-3-2-3 2-3-2-3 2z" />
          <path d="M9 8h6M9 12h6M9 16h3" />
        </>
      )}
      {name === 'out' && (
        <>
          <path d="M14 4h6v6" />
          <path d="M20 4l-9 9" />
          <path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" />
        </>
      )}
      {name === 'minus' && <path d="M5 12h14" />}
      {name === 'plus' && <path d="M12 5v14M5 12h14" />}
      {name === 'trash' && (
        <>
          <path d="M4.5 7h15" />
          <path d="M9.5 7V4.5h5V7" />
          <path d="M6.5 7l.9 12.1a1.8 1.8 0 0 0 1.8 1.7h5.6a1.8 1.8 0 0 0 1.8-1.7l.9-12.1" />
        </>
      )}
      {name === 'bag' && (
        <>
          <path d="M5.5 8h13l-1 12.5a1 1 0 0 1-1 .9H7.5a1 1 0 0 1-1-.9z" />
          <path d="M9 8V6.5a3 3 0 0 1 6 0V8" />
        </>
      )}
      {name === 'truck' && (
        <>
          <path d="M2.5 6.5h11v9.5h-11z" />
          <path d="M13.5 9.5h4l3 3.2v3.3h-7" />
          <circle cx="6.5" cy="17.5" r="1.7" />
          <circle cx="17" cy="17.5" r="1.7" />
        </>
      )}
      {name === 'alert' && (
        <>
          <path d="M12 3.5 2.5 20h19z" />
          <path d="M12 10v4.5M12 17.3v.2" />
        </>
      )}
      {name === 'grid' && (
        <>
          <rect x="3.5" y="3.5" width="4.5" height="4.5" rx="1" />
          <rect x="9.75" y="3.5" width="4.5" height="4.5" rx="1" />
          <rect x="16" y="3.5" width="4.5" height="4.5" rx="1" />
          <rect x="3.5" y="9.75" width="4.5" height="4.5" rx="1" />
          <rect x="9.75" y="9.75" width="4.5" height="4.5" rx="1" />
          <rect x="16" y="9.75" width="4.5" height="4.5" rx="1" />
          <rect x="3.5" y="16" width="4.5" height="4.5" rx="1" />
          <rect x="9.75" y="16" width="4.5" height="4.5" rx="1" />
          <rect x="16" y="16" width="4.5" height="4.5" rx="1" />
        </>
      )}
      {name === 'list' && <path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01" />}
      {name === 'table' && (
        <>
          <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
          <path d="M3.5 9.5h17M9.5 9.5v10M15 9.5v10" />
        </>
      )}
      {name === 'more' && <path d="M5.5 12h.01M12 12h.01M18.5 12h.01" strokeWidth={3} />}
      {name === 'search' && (
        <>
          <circle cx="11" cy="11" r="7" />
          <path d="M21 21l-4.35-4.35" />
        </>
      )}
    </svg>
  );
};

export const ChainMark: React.FC<{ code: string; name?: string }> = ({ code, name = '' }) => {
  return (
    <span
      className={`lg s-${code}`}
      style={{ background: `var(--s-${code}, var(--ink2))` }}
      aria-hidden="true"
    >
      {chainLetter(code, name)}
    </span>
  );
};

export const ProductPic: React.FC<{ photo?: string | null; title?: string }> = ({ photo, title }) => {
  const [broken, setBroken] = useState(false);
  const valid = !broken && photo && /^https?:\/\//i.test(photo);
  return (
    <span className="pic" title={title}>
      {valid ? (
        <img
          src={photo}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setBroken(true)}
        />
      ) : (
        <Icon name="bag" />
      )}
    </span>
  );
};

export const MoneyWhole: React.FC<{ value: number | null | undefined; big?: boolean }> = ({
  value,
  big = false,
}) => {
  const str = wholeNumberStr(value);
  if (str === null) return <>—</>;
  return big ? (
    <>
      {str}
      {NNBSP}
      <span className="r">₽</span>
    </>
  ) : (
    <>
      {str}
      {NNBSP}₽
    </>
  );
};

export const MoneyExact: React.FC<{ value: number | null | undefined }> = ({ value }) => {
  return <>{rub(value)}</>;
};
