import { NextResponse } from 'next/server';

// The /api routes write the settings (including MCP servers that run commands) and sign LiveKit tokens:
// they only answer this Mac. The host name also blocks "DNS rebinding" (a site that points its own domain
// at 127.0.0.1); the origin blocks a page from another site open in the browser.
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);

function hostname(value: string | null): string | null {
  if (!value) return null;
  try {
    return new URL(value.includes('://') ? value : `http://${value}`).hostname;
  } catch {
    return null;
  }
}

/** 403 response if the request does not come from this Mac, otherwise null. */
export function rejectIfNotLocal(req: Request): NextResponse | null {
  const host = hostname(req.headers.get('host'));
  const origin = req.headers.get('origin');
  const local = host !== null && LOCAL_HOSTS.has(host) && (origin === null || LOCAL_HOSTS.has(hostname(origin) ?? ''));
  return local ? null : new NextResponse('Accès réservé à ce Mac', { status: 403 });
}
