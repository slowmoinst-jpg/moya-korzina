import fs from 'node:fs';
import path from 'node:path';
import cookieParser from 'cookie-parser';
import express from 'express';
import { attachAuth } from './middleware/auth.js';
import { apiRouter } from './routes/api.js';

const PORT = Number(process.env.PORT || 3000);
const ROOT = process.cwd();
const IS_PROD = process.env.NODE_ENV === 'production';

async function startServer() {
  const app = express();
  app.disable('x-powered-by');
  app.set('trust proxy', true);

  app.use(cookieParser());
  app.use(express.urlencoded({ extended: true }));
  app.use(express.json({ limit: '2mb' }));
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
        setHeaders: (res) => {
          if (res.req && typeof res.req.query?.v === 'string') {
            res.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
          }
        },
      })
    );
  }

  // Proxy-aware Origin & CORS handling (supports AI Studio / Cloud Run x-forwarded-host and Android APK WebView)
  app.use((req, res, next) => {
    const origin = req.get('origin');
    const isApi = req.path.startsWith('/api/') || req.path === '/api';

    if (origin && isApi) {
      res.setHeader('Access-Control-Allow-Origin', origin);
      res.setHeader('Vary', 'Origin');
      res.setHeader('Access-Control-Allow-Credentials', 'true');
      res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, Accept');
      res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, PATCH, DELETE, OPTIONS');
      if (req.method === 'OPTIONS') {
        res.status(204).end();
        return;
      }
    }

    if (!['GET', 'HEAD', 'OPTIONS'].includes(req.method) && !isApi) {
      const allowedHosts = new Set(
        [
          req.get('host'),
          req.get('x-forwarded-host')?.split(',')[0]?.trim(),
          req.hostname,
        ].filter(Boolean) as string[]
      );
      if (origin && origin !== 'null' && !origin.startsWith('file://') && allowedHosts.size > 0) {
        try {
          const u = new URL(origin);
          const sameHost =
            allowedHosts.has(u.host) ||
            allowedHosts.has(u.hostname) ||
            u.hostname.endsWith('.run.app') ||
            u.hostname === 'localhost' ||
            u.hostname === '127.0.0.1';
          if (!sameHost) {
            res.status(403).json({ ok: false, error: 'Forbidden cross-origin request' });
            return;
          }
        } catch {
          res.status(403).json({ ok: false, error: 'Forbidden' });
          return;
        }
      }
    }
    next();
  });

  // Catch body-parser JSON syntax errors on /api/* as JSON instead of Express HTML
  app.use((err: unknown, req: express.Request, res: express.Response, next: express.NextFunction) => {
    if (req.path.startsWith('/api')) {
      const msg = err instanceof Error ? err.message : 'Некорректный формат запроса';
      res.status(400).json({ ok: false, error: msg });
      return;
    }
    next(err);
  });

  app.use(attachAuth);

  // Unified SPA JSON API
  app.use('/api', apiRouter);

  // Mount Vite middleware in dev, or serve pre-built SPA assets in prod
  let viteServer: {
    middlewares: express.RequestHandler;
    transformIndexHtml: (url: string, html: string) => Promise<string>;
  } | null = null;

  const clientDistDir = path.resolve(ROOT, 'dist', 'client');
  if (!IS_PROD) {
    const { createServer: createViteServer } = await import('vite');
    viteServer = await createViteServer({
      server: { middlewareMode: true, hmr: false },
      appType: 'custom',
    });
    app.use(viteServer.middlewares);
  } else if (fs.existsSync(clientDistDir)) {
    app.use(express.static(clientDistDir, { index: false }));
  }

  // No-store on dynamic HTML/API responses
  app.use((req, res, next) => {
    if (!req.path.startsWith('/static/') && !req.path.startsWith('/assets/')) {
      res.setHeader('Cache-Control', 'no-store');
    }
    next();
  });

  const serveSpaHtml = async (req: express.Request, res: express.Response, next: express.NextFunction) => {
    try {
      if (viteServer) {
        const rawHtml = fs.readFileSync(path.resolve(ROOT, 'index.html'), 'utf-8');
        const html = await viteServer.transformIndexHtml(req.originalUrl, rawHtml);
        res.status(200).setHeader('Content-Type', 'text/html; charset=utf-8').end(html);
        return;
      }
      const distIndex = path.resolve(clientDistDir, 'index.html');
      if (fs.existsSync(distIndex)) {
        res.sendFile(distIndex);
        return;
      }
      res.status(503).send('SPA bundle not built. Run npm run build.');
    } catch (err) {
      next(err);
    }
  };

  // Serve the single unified React SPA on all non-API GET routes (Express 5 safe)
  app.use((req, res, next) => {
    if (req.path.startsWith('/api/') || req.path === '/api') {
      res.status(404).json({ ok: false, error: 'Not found' });
      return;
    }
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.status(404).json({ ok: false, error: 'Not found' });
      return;
    }
    void serveSpaHtml(req, res, next);
  });

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`Моя корзина SPA + API server listening on http://0.0.0.0:${PORT}`);
  });
}

void startServer();
