'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

// Same structure as web/agent/settings.py (source of the default values).
export type VoiceSettings = {
  llm: {
    model: string;
    system_prompt: string;
    temperature: number;
    top_p: number;
    max_tokens: number;
    memory_turns: number;
  };
  stt: { model: 'tiny' | 'small' | 'medium'; streaming: boolean; vad_window: number; update_interval: number };
  turn: {
    vad_threshold: number;
    min_silence: number;
    turn_detector: boolean;
    min_endpointing_delay: number;
    max_endpointing_delay: number;
    allow_interruptions: boolean;
    min_interruption_duration: number;
    min_interruption_words: number;
  };
  tts: {
    engine: 'kokoro' | 'qwen3-0.6b' | 'qwen3-1.7b' | 'chatterbox-turbo';
    kokoro_voice: string;
    qwen_speaker: string;
    speed: number;
  };
  emotion: { mode: 'off' | 'fixed' | 'auto'; fixed_style: string; instructions: string; sounds: boolean };
  tools: {
    enabled: string[];
    screen_context: boolean;
    accounts: Record<string, string>;
    claude_code: { project_dir: string; permission_mode: 'plan' | 'acceptEdits'; timeout_minutes: number };
    mcp_servers: Record<string, unknown>[];
  };
  ui: {
    visualizer: 'bar' | 'wave' | 'grid' | 'radial' | 'aura';
    theme: ThemeName;
    show_metrics: boolean;
  };
};

export type ThemeName = 'blue' | 'indigo' | 'purple' | 'teal' | 'green' | 'orange' | 'graphite';

// Apple system colors (dark mode), used by the visualizers and the interface accent.
export const THEME_COLORS: Record<ThemeName, `#${string}`> = {
  blue: '#0A84FF',
  indigo: '#5E5CE6',
  purple: '#BF5AF2',
  teal: '#40C8E0',
  green: '#30D158',
  orange: '#FF9F0A',
  graphite: '#8E8E93',
};

/** Settings read by the agent only at the start of a conversation. */
export function nextSessionChanges(draft: VoiceSettings, saved: VoiceSettings): string[] {
  const changed: string[] = [];
  if (JSON.stringify(draft.stt) !== JSON.stringify(saved.stt)) changed.push('transcription');
  if (JSON.stringify(draft.turn) !== JSON.stringify(saved.turn)) changed.push('tour de parole');
  if (JSON.stringify(draft.tools) !== JSON.stringify(saved.tools)) changed.push('outils');
  const autoBefore = saved.emotion.mode === 'auto';
  const autoAfter = draft.emotion.mode === 'auto';
  if (
    autoBefore !== autoAfter ||
    draft.emotion.instructions !== saved.emotion.instructions ||
    draft.emotion.sounds !== saved.emotion.sounds
  ) {
    changed.push('émotions automatiques');
  }
  return changed;
}

type SettingsContextValue = {
  settings: VoiceSettings | null;
  defaults: VoiceSettings | null;
  error: string | null;
  save: (next: VoiceSettings) => Promise<void>;
};

const SettingsContext = createContext<SettingsContextValue | null>(null);

export function SettingsProvider({ children }: { children: React.ReactNode }) {
  const [settings, setSettings] = useState<VoiceSettings | null>(null);
  const [defaults, setDefaults] = useState<VoiceSettings | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/settings', { cache: 'no-store' })
      .then((r) => r.json())
      .then((data) => {
        setSettings(data.settings);
        setDefaults(data.defaults);
      })
      .catch(() => setError('Réglages indisponibles'));
  }, []);

  const save = useCallback(async (next: VoiceSettings) => {
    const r = await fetch('/api/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(next),
    });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    setSettings(data.settings);
  }, []);

  const value = useMemo(() => ({ settings, defaults, error, save }), [settings, defaults, error, save]);
  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useSettings() {
  const ctx = useContext(SettingsContext);
  if (!ctx) throw new Error('useSettings hors de SettingsProvider');
  return ctx;
}
