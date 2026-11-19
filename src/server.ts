import fs from 'node:fs';
import path from 'node:path';
import cookieParser from 'cookie-parser';
import express from 'express';
import {
  cheques,
  escapeHtml,
  goods,
  humanDay,
  icon,
  mark,
  num,
  pct,
  pic,
  pluralItems,
  qtyLabel,
  rub,
  staticV,
  unitLabel,
  where,
  whole,
  whom,
} from './helpers.js';
import { attachAuth } from './middleware/auth.js';
import { phoneRouter } from './routes/phone.js';
import { SCREENS, screensOf, SECTIONS, webRouter } from './routes/web.js';

const PORT = Number(process.env.PORT || 3000);
const ROOT = process.cwd();

const app = express();
app.disable('x-powered-by');
app.set('trust proxy', true);
app.set('view engine', 'ejs');
app.set('views', path.resolve(ROOT, 'views'));

// Expose helpers to all EJS templates
Object.assign(app.locals, {
  icon,
  mark,
  pic,
  whole,
  rub,
  num,
  pct,
  unitLabel,
  qtyLabel,
  goods,
  pluralItems,
  cheques,
  where,
  whom,
  humanDay,
  staticV,
  escapeHtml,
  SECTIONS,
  SCREENS,
  screensOf,
});

app.use(cookieParser());
app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use((req, _res, next) => {
  if (!req.body) req.body = {};
  next();
});

// Static files under /static (served from /public/static)
const staticDir = path.resolve(ROOT, 'public', 'static');
if (fs.existsSync(staticDir)) {
  app.use(
    '/static',
    express.static(staticDir, {
      setHeaders: (res, _filePath, _stat) => {
        if (res.req && typeof res.req.query?.v === 'string') {
          res.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
        }
      },
    })
  );
}

// Same-origin safety check on mutating requests
app.use((req, res, next) => {
  if (!['GET', 'HEAD', 'OPTIONS'].includes(req.method)) {
    const origin = req.get('origin');
    const host = req.get('host');
    if (origin && host) {
      try {
        const u = new URL(origin);
        if (u.host !== host) {
          res.status(403).send('Forbidden cross-origin request');
          return;
        }
      } catch {
        res.status(403).send('Forbidden');
        return;
      }
    }
  }
  next();
});

app.use(attachAuth);

// No-store on dynamic HTML pages so basket/auth state never serves stale cache
app.use((req, res, next) => {
  if (!req.path.startsWith('/static/')) {
    res.setHeader('Cache-Control', 'no-store');
  }
  next();
});

app.use('/phone', phoneRouter);
app.use('/', webRouter);

app.use((_req, res) => {
  res.status(404).render('web/lost', { screen: null });
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Моя корзина server listening on http://0.0.0.0:${PORT}`);
});
