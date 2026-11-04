import { promises as fs } from 'node:fs';
import path from 'node:path';
import { NextResponse } from 'next/server';
import { rejectIfNotLocal } from '@/lib/local-only';

// Settings shared with the agent (web/agent/settings.py).
// Default values come from the agent (settings.defaults.json), single source.
const WEB_DIR = path.resolve(process.cwd(), '..');
const SETTINGS_PATH = path.join(WEB_DIR, 'settings.json');
const DEFAULTS_PATH = path.join(WEB_DIR, 'settings.defaults.json');

export const revalidate = 0;

type Json = Record<string, unknown>;

async function readJson(file: string): Promise<Json> {
  try {
    return JSON.parse(await fs.readFile(file, 'utf8')) as Json;
  } catch {
    return {};
  }
}

function merge(base: Json, override: Json): Json {
  const out: Json = { ...base };
  for (const [key, value] of Object.entries(override)) {
    const current = out[key];
    out[key] =
      value && typeof value === 'object' && !Array.isArray(value) && current && typeof current === 'object'
        ? merge(current as Json, value as Json)
        : value;
  }
  return out;
}

export async function GET(req: Request) {
  const denied = rejectIfNotLocal(req);
  if (denied) return denied;
  const defaults = await readJson(DEFAULTS_PATH);
  const saved = await readJson(SETTINGS_PATH);
  return NextResponse.json(
    { defaults, settings: merge(defaults, saved) },
    { headers: { 'Cache-Control': 'no-store' } }
  );
}

export async function PUT(req: Request) {
  const denied = rejectIfNotLocal(req);
  if (denied) return denied;
  const body = (await req.json()) as Json;
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return new NextResponse('Réglages invalides', { status: 400 });
  }
  const defaults = await readJson(DEFAULTS_PATH);
  const settings = merge(defaults, body);
  // Atomic write: the agent never reads a half-written file.
  const tmp = `${SETTINGS_PATH}.tmp`;
  await fs.writeFile(tmp, JSON.stringify(settings, null, 2));
  await fs.rename(tmp, SETTINGS_PATH);
  return NextResponse.json({ settings });
}
