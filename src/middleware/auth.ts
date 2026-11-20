import crypto from 'node:crypto';
import type { NextFunction, Request, Response } from 'express';
import { DEMO_PHONE, formatPhoneDisplay, getWorkspace, normalizePhone } from '../data/store.js';
import type { Workspace } from '../types.js';

const COOKIE_NAME = 'korzina_session';
const FAIL_LIMIT = 5;
const FAIL_WINDOW_MS = 15 * 60 * 1000;
const failMap = new Map<string, number[]>();

const SESSION_SECRET =
  process.env.SESSION_SECRET && process.env.SESSION_SECRET.length >= 16
    ? process.env.SESSION_SECRET
    : crypto.randomBytes(32).toString('hex');

export interface AuthLocals {
  phone: string | null;
  phoneLabel: string;
  isDemo: boolean;
  devicePhone: boolean;
  deviceApp: boolean;
  workspace: Workspace | null;
}

function sign(payload: string): string {
  const b64 = Buffer.from(payload, 'utf8').toString('base64url');
  const sig = crypto.createHmac('sha256', SESSION_SECRET).update(b64).digest('base64url');
  return `${b64}.${sig}`;
}

function verify(token: string | undefined): string | null {
  if (!token || typeof token !== 'string') return null;
  const dot = token.indexOf('.');
  if (dot <= 0) return null;
  const b64 = token.slice(0, dot);
  const sig = token.slice(dot + 1);
  const expected = crypto.createHmac('sha256', SESSION_SECRET).update(b64).digest('base64url');
  const a = Buffer.from(sig);
  const b = Buffer.from(expected);
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null;
  try {
    const parsed = JSON.parse(Buffer.from(b64, 'base64url').toString('utf8'));
    if (typeof parsed.phone === 'string') return normalizePhone(parsed.phone);
  } catch {
    return null;
  }
  return null;
}

function isHttpsRequest(req?: Request): boolean {
  if (!req) return false;
  if (req.secure) return true;
  const proto = String(req.get('x-forwarded-proto') || '').split(',')[0].trim().toLowerCase();
  return proto === 'https';
}

export function createSessionToken(phone: string): string {
  return sign(JSON.stringify({ phone, since: Date.now() }));
}

export function signInCookie(res: Response, phone: string, req?: Request): string {
  const token = createSessionToken(phone);
  const secure = isHttpsRequest(req);
  res.cookie(COOKIE_NAME, token, {
    httpOnly: true,
    sameSite: secure ? 'none' : 'lax',
    secure,
    path: '/',
    maxAge: 30 * 24 * 3600 * 1000,
  });
  return token;
}

export function signOutCookie(res: Response, req?: Request): void {
  const secure = isHttpsRequest(req);
  res.clearCookie(COOKIE_NAME, {
    path: '/',
    sameSite: secure ? 'none' : 'lax',
    secure,
  });
}

export function isPhoneDevice(req: Request): boolean {
  const ua = req.get('user-agent') || '';
  return /Android|iPhone|iPad|Mobile|Korzina/i.test(ua);
}

export function isDeviceApp(req: Request): boolean {
  const ua = req.get('user-agent') || '';
  return /KorzinaApp|KorzinaDevice/i.test(ua);
}

export function safeNextPath(raw: unknown): string {
  const value = typeof raw === 'string' ? raw.trim() : '';
  if (
    value.startsWith('/') &&
    !value.startsWith('//') &&
    !value.includes('\\') &&
    !/[\x00-\x1f]/.test(value)
  ) {
    return value;
  }
  return '/';
}

export function checkLoginRateLimit(req: Request): boolean {
  const ip = req.ip || 'unknown';
  const now = Date.now();
  const list = (failMap.get(ip) || []).filter((t) => now - t < FAIL_WINDOW_MS);
  if (list.length > 0) failMap.set(ip, list);
  else failMap.delete(ip);
  return list.length >= FAIL_LIMIT;
}

export function recordLoginFailure(req: Request): void {
  const ip = req.ip || 'unknown';
  const now = Date.now();
  const list = (failMap.get(ip) || []).filter((t) => now - t < FAIL_WINDOW_MS);
  list.push(now);
  failMap.set(ip, list);
}

export function pinIsSet(): boolean {
  return Boolean(process.env.INSTALL_PIN && process.env.INSTALL_PIN.trim().length > 0);
}

export function checkPin(raw: string): boolean {
  const expected = (process.env.INSTALL_PIN || '').trim();
  if (!expected) return true;
  return raw.trim() === expected;
}

export function attachAuth(req: Request, res: Response, next: NextFunction): void {
  const authHeader = req.get('authorization') || '';
  const bearerToken = authHeader.startsWith('Bearer ') ? authHeader.slice(7).trim() : undefined;
  const cookieToken = req.cookies?.[COOKIE_NAME];
  const phone = verify(bearerToken) || verify(cookieToken);
  const ws = phone ? getWorkspace(phone) : null;
  const locals: AuthLocals = {
    phone,
    phoneLabel: phone ? formatPhoneDisplay(phone) : '',
    isDemo: phone === DEMO_PHONE,
    devicePhone: isPhoneDevice(req),
    deviceApp: isDeviceApp(req),
    workspace: ws,
  };
  res.locals.auth = locals;
  res.locals.phone = locals.phone;
  res.locals.phone_label = locals.phoneLabel;
  res.locals.is_demo = locals.isDemo;
  res.locals.device_phone = locals.devicePhone;
  res.locals.device_app = locals.deviceApp;
  res.locals.on_phone = locals.devicePhone;
  res.locals.address = ws?.address ?? null;
  next();
}

export function requirePhone(req: Request, res: Response, next: NextFunction): void {
  const auth = res.locals.auth as AuthLocals;
  if (!auth.phone || !auth.workspace) {
    if (req.accepts(['html', 'json']) === 'json' || req.path.startsWith('/api/')) {
      res.status(401).json({ ok: false, error: 'Требуется вход' });
      return;
    }
    const target = encodeURIComponent(req.originalUrl || '/');
    res.redirect(`/login?next=${target}`);
    return;
  }

  // Demo read-only guard on mutating requests (except logout/login/demo)
  if (
    auth.isDemo &&
    !['GET', 'HEAD', 'OPTIONS'].includes(req.method) &&
    !['/logout', '/demo', '/login'].includes(req.path)
  ) {
    if (req.is('application/json') || req.accepts(['html', 'json']) === 'json') {
      res.status(403).json({ ok: false, error: 'Это демо — изменения не сохраняются' });
      return;
    }
    let back = '/';
    const ref = req.get('referer') || '';
    try {
      if (ref) {
        const u = new URL(ref);
        if (u.host === req.get('host')) {
          back = safeNextPath(u.pathname + u.search);
        }
      }
    } catch {
      back = '/';
    }
    const sep = back.includes('?') ? '&' : '?';
    res.redirect(303, `${back.replace(/[?&]demo=1\b/g, '')}${sep}demo=1`);
    return;
  }

  next();
}
