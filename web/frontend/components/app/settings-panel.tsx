'use client';

import { useEffect, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { useSessionContext } from '@livekit/components-react';
import { Check, RotateCcw, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/shadcn/utils';
import {
  THEME_COLORS,
  type ThemeName,
  type VoiceSettings,
  nextSessionChanges,
  useSettings,
} from '@/lib/settings';

// "local:<folder>": copies in web/models with a fixed tool template (web/agent/fix_qwen_template.py).

const LLM_MODELS = [
  ['mlx-community/Qwen3-4B-Instruct-2507-4bit', 'Qwen3 4B · outils, le plus fiable'],
  ['local:Qwen2.5-3B-Instruct-4bit-tools', 'Qwen2.5 3B · outils (licence non commerciale)'],
  ['local:Qwen2.5-1.5B-Instruct-4bit-tools', 'Qwen2.5 1.5B · outils, plus rapide'],
  ['mlx-community/Qwen2.5-0.5B-Instruct-4bit', 'Qwen2.5 0.5B · très rapide, sans outils'],
  ['mlx-community/Qwen2.5-7B-Instruct-4bit', 'Qwen2.5 7B · meilleur, plus lent'],
  ['mlx-community/Llama-3.2-1B-Instruct-4bit', 'Llama 3.2 1B · sans outils'],
  ['mlx-community/Llama-3.2-3B-Instruct-4bit', 'Llama 3.2 3B · sans outils'],
  ['mlx-community/gemma-3-4b-it-qat-4bit', 'Gemma 3 4B · sans outils'],
] as const;

const TTS_ENGINES = [
  ['qwen3-0.6b', 'Qwen3-TTS 0.6B · émotions, ~temps réel'],
  ['qwen3-1.7b', 'Qwen3-TTS 1.7B · émotions, plus stable, plus lent'],
  ['kokoro', 'Kokoro · le plus rapide, sans émotion'],
  ['chatterbox-turbo', 'Chatterbox Turbo · sans émotion'],
] as const;

const QWEN_SPEAKERS = ['Ryan', 'Aiden', 'Vivian', 'Serena', 'Dylan', 'Eric', 'Uncle_Fu'];

const KOKORO_VOICES = [
  'af_heart', 'af_alloy', 'af_aoede', 'af_bella', 'af_jessica', 'af_kore', 'af_nicole', 'af_nova', 'af_river',
  'af_sarah', 'af_sky', 'am_adam', 'am_echo', 'am_eric', 'am_fenrir', 'am_liam', 'am_michael', 'am_onyx',
  'am_puck', 'am_santa', 'bf_alice', 'bf_emma', 'bf_isabella', 'bf_lily', 'bm_daniel', 'bm_fable',
  'bm_george', 'bm_lewis',
];

const TOOLS = [
  ['open_app', 'Ouvrir une application'],
  ['quit_app', 'Quitter une application'],
  ['place_window', 'Placer une fenêtre (gauche, droite, plein écran)'],
  ['read_selection', 'Lire le texte sélectionné'],
  ['insert_text', 'Écrire au curseur (jamais envoyé)'],
  ['read_screen', 'Lire la fenêtre affichée (OCR local)'],
  ['look_at_screen', "Regarder l'écran (image, modèle de vision local)"],
  ['calendar_events', 'Calendrier (déplacer / supprimer : confirmation)'],
  ['add_reminder', 'Ajouter un rappel'],
  ['open_website', 'Ouvrir un site web'],
  ['search_web', 'Chercher sur le web'],
  ['open_folder', 'Ouvrir un dossier'],
  ['set_volume', 'Régler le volume'],
  ['get_current_time', "Donner l'heure et la date"],
  ['list_open_apps', 'Voir les applications ouvertes'],
  ['search_youtube', 'Chercher sur YouTube (onglet ouvert réutilisé)'],
  ['play_youtube_video', 'Lancer une vidéo YouTube'],
  ['draft_email', 'Brouillon Gmail (jamais envoyé)'],
  ['ask_claude_code', 'Déléguer à Claude Code (cloud)'],
  ['open_claude_session', 'Ouvrir la session Claude Code dans Terminal'],
  ['open_terminal', 'Nouveau Terminal (avec Claude Code au besoin)'],
  ['media_control', 'Pause, lecture, suivant, plein écran'],
  ['close_tab', "Fermer l'onglet Chrome affiché"],
  ['search_work_memory', 'Mémoire de travail (claude-mem, si installé)'],
] as const;

/** JSON field: free text, applied to the draft only if it is valid. */
function JsonField(props: { label: string; value: unknown; help?: string; onChange: (v: never) => void }) {
  const [text, setText] = useState(() => JSON.stringify(props.value, null, 2));
  const [invalid, setInvalid] = useState(false);
  return (
    <Row>
      <span className="text-sm">{props.label}</span>
      <textarea
        value={text}
        rows={5}
        spellCheck={false}
        onChange={(e) => {
          setText(e.target.value);
          try {
            props.onChange(JSON.parse(e.target.value) as never);
            setInvalid(false);
          } catch {
            setInvalid(true);
          }
        }}
        className={cn(
          'bg-muted/60 mt-2 w-full resize-y rounded-xl border px-3 py-2 font-mono text-xs leading-relaxed',
          invalid ? 'border-destructive' : 'border-transparent'
        )}
      />
      {props.help && <p className="text-muted-foreground mt-1 text-xs leading-relaxed">{props.help}</p>}
    </Row>
  );
}

const FIXED_STYLES = [
  'gently warm and calm',
  'really bright and upbeat',
  'soft, almost a whisper',
  'calm and professional',
  'a little playful, friendly',
];

const VISUALIZERS = [
  ['bar', 'Barres'],
  ['wave', 'Onde'],
  ['grid', 'Grille'],
  ['radial', 'Radial'],
  ['aura', 'Aura'],
] as const;

/** "af_heart" -> "Heart · US · femme" (the label shown in the UI). */
function voiceLabel(id: string) {
  const [prefix, name] = id.split('_');
  if (!name || prefix.length !== 2) return id;
  const accent = prefix[0] === 'b' ? 'UK' : 'US';
  const gender = prefix[1] === 'm' ? 'homme' : 'femme';
  return `${name[0].toUpperCase()}${name.slice(1)} · ${accent} · ${gender}`;
}

// MARK: - Fields

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h3 className="text-muted-foreground px-1 text-xs font-semibold tracking-wide uppercase">{title}</h3>
      <div className="bg-card/70 divide-border/60 divide-y rounded-2xl border border-white/10 backdrop-blur-xl">
        {children}
      </div>
      {hint && <p className="text-muted-foreground px-1 text-xs leading-relaxed">{hint}</p>}
    </section>
  );
}

function Row({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn('px-4 py-3', className)}>{children}</div>;
}

function SliderField(props: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format?: (v: number) => string;
  help?: string;
  onChange: (v: number) => void;
}) {
  const { label, value, min, max, step, format = (v) => v.toFixed(2), help, onChange } = props;
  return (
    <Row>
      <div className="flex items-center justify-between gap-3 text-sm">
        <span>{label}</span>
        <span className="text-muted-foreground font-mono text-xs tabular-nums">{format(value)}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="accent-primary mt-2 w-full"
      />
      {help && <p className="text-muted-foreground mt-1 text-xs leading-relaxed">{help}</p>}
    </Row>
  );
}

function ToggleField(props: { label: string; checked: boolean; help?: string; onChange: (v: boolean) => void }) {
  const { label, checked, help, onChange } = props;
  return (
    <Row>
      <label className="flex cursor-pointer items-center justify-between gap-3 text-sm">
        <span>{label}</span>
        <button
          type="button"
          role="switch"
          aria-checked={checked}
          onClick={() => onChange(!checked)}
          className={cn(
            'relative h-7 w-12 shrink-0 rounded-full transition-colors',
            checked ? 'bg-primary' : 'bg-muted-foreground/30'
          )}
        >
          <span
            className={cn(
              'absolute top-0.5 left-0 size-6 rounded-full bg-white shadow transition-transform',
              checked ? 'translate-x-5.5' : 'translate-x-0.5'
            )}
          />
        </button>
      </label>
      {help && <p className="text-muted-foreground mt-1 text-xs leading-relaxed">{help}</p>}
    </Row>
  );
}

function SelectField(props: {
  label: string;
  value: string;
  options: readonly (readonly [string, string])[];
  help?: string;
  onChange: (v: string) => void;
}) {
  const { label, value, options, help, onChange } = props;
  return (
    <Row>
      <label className="flex items-center justify-between gap-3 text-sm">
        <span className="shrink-0">{label}</span>
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="bg-muted/60 min-w-0 flex-1 truncate rounded-lg border-0 px-2 py-1.5 text-right text-sm"
        >
          {options.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
          {!options.some(([v]) => v === value) && <option value={value}>{value}</option>}
        </select>
      </label>
      {help && <p className="text-muted-foreground mt-1 text-xs leading-relaxed">{help}</p>}
    </Row>
  );
}

function TextField(props: { label: string; value: string; rows?: number; help?: string; onChange: (v: string) => void }) {
  const { label, value, rows = 4, help, onChange } = props;
  return (
    <Row>
      <span className="text-sm">{label}</span>
      <textarea
        value={value}
        rows={rows}
        onChange={(e) => onChange(e.target.value)}
        className="bg-muted/60 mt-2 w-full resize-y rounded-xl border-0 px-3 py-2 text-sm leading-relaxed"
      />
      {help && <p className="text-muted-foreground mt-1 text-xs leading-relaxed">{help}</p>}
    </Row>
  );
}

function Segmented<T extends string>(props: {
  value: T;
  options: readonly (readonly [T, string])[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="bg-muted/60 grid auto-cols-fr grid-flow-col gap-1 rounded-xl p-1">
      {props.options.map(([v, l]) => (
        <button
          key={v}
          type="button"
          onClick={() => props.onChange(v)}
          className={cn(
            'rounded-lg px-2 py-1.5 text-xs font-medium transition-colors',
            props.value === v ? 'bg-background shadow-sm' : 'text-muted-foreground'
          )}
        >
          {l}
        </button>
      ))}
    </div>
  );
}

// MARK: - Panel

const ms = (v: number) => `${Math.round(v * 1000)} ms`;
const sec = (v: number) => `${v.toFixed(2)} s`;
const int = (v: number) => `${Math.round(v)}`;

export function SettingsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { settings, defaults, save } = useSettings();
  const session = useSessionContext();
  const [draft, setDraft] = useState<VoiceSettings | null>(settings);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open && settings) setDraft(structuredClone(settings));
  }, [open, settings]);

  const dirty = useMemo(
    () => !!draft && !!settings && JSON.stringify(draft) !== JSON.stringify(settings),
    [draft, settings]
  );
  const later = draft && settings && session.isConnected ? nextSessionChanges(draft, settings) : [];

  function set<K extends keyof VoiceSettings>(group: K, patch: Partial<VoiceSettings[K]>) {
    setDraft((d) => (d ? { ...d, [group]: { ...d[group], ...patch } } : d));
  }

  async function onSave() {
    if (!draft) return;
    setSaving(true);
    try {
      await save(draft);
    } finally {
      setSaving(false);
    }
  }

  return (
    <AnimatePresence>
      {open && draft && (
        <>
          <motion.div
            key="backdrop"
            className="fixed inset-0 z-[60] bg-black/30 backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.aside
            key="panel"
            role="dialog"
            aria-label="Réglages"
            className="bg-background/80 fixed inset-y-0 right-0 z-[70] flex w-full flex-col border-l border-white/10 shadow-2xl backdrop-blur-2xl sm:max-w-md"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', damping: 32, stiffness: 320 }}
          >
            <header className="flex items-center justify-between px-5 pt-5 pb-3">
              <h2 className="text-xl font-semibold">Réglages</h2>
              <Button variant="ghost" size="icon" onClick={onClose} aria-label="Fermer">
                <X />
              </Button>
            </header>

            <div className="flex-1 space-y-7 overflow-y-auto px-4 pb-32">
              <Section title="Apparence" hint="Visualiseurs officiels LiveKit : ils réagissent à l'état de l'agent et au son.">
                <Row>
                  <Segmented
                    value={draft.ui.visualizer}
                    options={VISUALIZERS}
                    onChange={(v) => set('ui', { visualizer: v })}
                  />
                </Row>
                <Row className="flex flex-wrap justify-center gap-3">
                  {(Object.keys(THEME_COLORS) as ThemeName[]).map((t) => (
                    <button
                      key={t}
                      type="button"
                      aria-label={t}
                      onClick={() => set('ui', { theme: t })}
                      className="flex size-8 items-center justify-center rounded-full shadow-inner"
                      style={{ background: THEME_COLORS[t] }}
                    >
                      {draft.ui.theme === t && <Check className="size-4 text-white" />}
                    </button>
                  ))}
                </Row>
                <ToggleField
                  label="Afficher les mesures"
                  checked={draft.ui.show_metrics}
                  onChange={(v) => set('ui', { show_metrics: v })}
                />
              </Section>

              <Section title="Prompt système" hint="Appliqué dès la prochaine réponse.">
                <TextField
                  label="Instructions données au modèle"
                  value={draft.llm.system_prompt}
                  rows={6}
                  onChange={(v) => set('llm', { system_prompt: v })}
                />
                {defaults && draft.llm.system_prompt !== defaults.llm.system_prompt && (
                  <Row>
                    <button
                      type="button"
                      className="text-primary text-sm"
                      onClick={() => set('llm', { system_prompt: defaults.llm.system_prompt })}
                    >
                      Revenir au prompt par défaut
                    </button>
                  </Row>
                )}
              </Section>

              <Section
                title="Émotions"
                hint="Automatique : le LLM écrit un ton avant chaque phrase, Qwen3-TTS le joue. Seuls les moteurs Qwen3-TTS savent jouer une émotion."
              >
                <Row>
                  <Segmented
                    value={draft.emotion.mode}
                    options={[
                      ['off', 'Désactivées'],
                      ['fixed', 'Style fixe'],
                      ['auto', 'Automatiques'],
                    ]}
                    onChange={(v) => set('emotion', { mode: v })}
                  />
                </Row>
                {draft.emotion.mode === 'fixed' && (
                  <>
                    <TextField
                      label="Style de voix"
                      value={draft.emotion.fixed_style}
                      rows={2}
                      help="En anglais, 2 à 6 mots : humeur, volume, rythme."
                      onChange={(v) => set('emotion', { fixed_style: v })}
                    />
                    <Row className="flex flex-wrap gap-2">
                      {FIXED_STYLES.map((style) => (
                        <button
                          key={style}
                          type="button"
                          onClick={() => set('emotion', { fixed_style: style })}
                          className="bg-muted/60 rounded-full px-3 py-1 text-xs"
                        >
                          {style}
                        </button>
                      ))}
                    </Row>
                  </>
                )}
                {draft.emotion.mode === 'auto' && (
                  <>
                    <TextField
                      label="Consigne d'émotion pour le LLM"
                      value={draft.emotion.instructions}
                      rows={7}
                      help="Ajoutée au prompt système. Elle explique au LLM comment écrire le ton de chaque phrase."
                      onChange={(v) => set('emotion', { instructions: v })}
                    />
                    <ToggleField
                      label="Sons non verbaux"
                      checked={draft.emotion.sounds}
                      help="Autorise le LLM à écrire des sons (rire, soupir). Qwen3-TTS ne les produit pas : ils sont retirés du texte."
                      onChange={(v) => set('emotion', { sounds: v })}
                    />
                  </>
                )}
              </Section>

              <Section
                title="Outils"
                hint="Actions sur le Mac : « open Chrome », « open my downloads folder », « set the volume to 30 ». Il faut un LLM « outils » (Qwen2.5 3B ou 1.5B). S'applique à la prochaine conversation."
              >
                {TOOLS.map(([id, label]) => (
                  <ToggleField
                    key={id}
                    label={label}
                    checked={draft.tools.enabled.includes(id)}
                    onChange={(on) =>
                      set('tools', {
                        enabled: on
                          ? [...draft.tools.enabled.filter((t) => t !== id), id]
                          : draft.tools.enabled.filter((t) => t !== id),
                      })
                    }
                  />
                ))}
                <ToggleField
                  label="Savoir ce qui est ouvert à l'écran"
                  checked={draft.tools.screen_context}
                  help="Une ligne par question : app au premier plan et apps ouvertes (~30 tokens), sans capture d'écran."
                  onChange={(v) => set('tools', { screen_context: v })}
                />
                <JsonField
                  label="Comptes Google (étiquette → profil Chrome)"
                  value={draft.tools.accounts}
                  help='Ex. { "personal": "Default", "work": "Profile 1" }. Le tableau de bord Mac les détecte automatiquement.'
                  onChange={(v) => set('tools', { accounts: v })}
                />
                <TextField
                  label="Claude Code : dossier du projet"
                  value={draft.tools.claude_code.project_dir}
                  rows={1}
                  onChange={(v) => set('tools', { claude_code: { ...draft.tools.claude_code, project_dir: v } })}
                />
                <Row>
                  <Segmented
                    value={draft.tools.claude_code.permission_mode}
                    options={[
                      ['plan', 'Claude Code : lecture seule'],
                      ['acceptEdits', 'Peut modifier'],
                    ]}
                    onChange={(v) => set('tools', { claude_code: { ...draft.tools.claude_code, permission_mode: v } })}
                  />
                </Row>
                <JsonField
                  label="Mes serveurs MCP"
                  value={draft.tools.mcp_servers}
                  help='Ex. [{ "name": "fichiers", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "~/Documents"], "allowed_tools": ["read_file", "list_directory"] }] ou [{ "name": "x", "url": "http://localhost:9000/mcp" }].'
                  onChange={(v) => set('tools', { mcp_servers: v })}
                />
              </Section>

              <Section title="Modèle de langage" hint="Chargé par le serveur LLM au premier usage (téléchargement la première fois).">
                <SelectField
                  label="Modèle"
                  value={draft.llm.model}
                  options={LLM_MODELS}
                  onChange={(v) => set('llm', { model: v })}
                />
                <SliderField
                  label="Température"
                  value={draft.llm.temperature}
                  min={0}
                  max={2}
                  step={0.05}
                  help="0 = toujours la même réponse ; plus haut = plus varié."
                  onChange={(v) => set('llm', { temperature: v })}
                />
                <SliderField
                  label="Top-p"
                  value={draft.llm.top_p}
                  min={0.05}
                  max={1}
                  step={0.05}
                  help="Ne garde que les mots les plus probables jusqu'à ce cumul. 1 = désactivé."
                  onChange={(v) => set('llm', { top_p: v })}
                />
                <SliderField
                  label="Longueur max"
                  value={draft.llm.max_tokens}
                  min={16}
                  max={512}
                  step={8}
                  format={(v) => `${Math.round(v)} tokens`}
                  onChange={(v) => set('llm', { max_tokens: v })}
                />
                <SliderField
                  label="Mémoire"
                  value={draft.llm.memory_turns}
                  min={1}
                  max={30}
                  step={1}
                  format={(v) => `${Math.round(v)} échanges`}
                  help="Nombre de questions et réponses précédentes gardées par le modèle."
                  onChange={(v) => set('llm', { memory_turns: v })}
                />
              </Section>

              <Section title="Voix" hint="Changement de moteur : chargé par le serveur de voix à la phrase suivante.">
                <SelectField
                  label="Moteur"
                  value={draft.tts.engine}
                  options={TTS_ENGINES}
                  onChange={(v) => set('tts', { engine: v as VoiceSettings['tts']['engine'] })}
                />
                {draft.tts.engine.startsWith('qwen3') && (
                  <SelectField
                    label="Voix Qwen"
                    value={draft.tts.qwen_speaker}
                    options={QWEN_SPEAKERS.map((s) => [s, s] as const)}
                    help="Ryan et Aiden sont les voix anglaises natives."
                    onChange={(v) => set('tts', { qwen_speaker: v })}
                  />
                )}
                {draft.tts.engine === 'kokoro' && (
                  <>
                    <SelectField
                      label="Voix Kokoro"
                      value={draft.tts.kokoro_voice}
                      options={KOKORO_VOICES.map((v) => [v, voiceLabel(v)] as const)}
                      onChange={(v) => set('tts', { kokoro_voice: v })}
                    />
                    <SliderField
                      label="Vitesse"
                      value={draft.tts.speed}
                      min={0.5}
                      max={2}
                      step={0.05}
                      format={(v) => `×${v.toFixed(2)}`}
                      onChange={(v) => set('tts', { speed: v })}
                    />
                  </>
                )}
              </Section>

              <Section title="Transcription · Moonshine" hint="S'applique à la prochaine conversation.">
                <SelectField
                  label="Modèle"
                  value={draft.stt.model}
                  options={[
                    ['tiny', 'Tiny · le plus rapide'],
                    ['small', 'Small · plus précis'],
                    ['medium', 'Medium · le plus précis, lourd'],
                  ]}
                  onChange={(v) => set('stt', { model: v as VoiceSettings['stt']['model'] })}
                />
                <ToggleField
                  label="Transcription en direct"
                  checked={draft.stt.streaming}
                  help="Transcrit pendant que tu parles : texte partiel affiché et fin de phrase plus rapide."
                  onChange={(v) => set('stt', { streaming: v })}
                />
                <SliderField
                  label="Silence de fin de phrase"
                  value={draft.stt.vad_window}
                  min={0.1}
                  max={1}
                  step={0.05}
                  format={sec}
                  help="Silence que Moonshine attend avant de clore la phrase. Plus court = plus rapide, risque de couper."
                  onChange={(v) => set('stt', { vad_window: v })}
                />
                <SliderField
                  label="Intervalle de mise à jour"
                  value={draft.stt.update_interval}
                  min={0.1}
                  max={1}
                  step={0.05}
                  format={sec}
                  onChange={(v) => set('stt', { update_interval: v })}
                />
              </Section>

              <Section title="Tour de parole" hint="S'applique à la prochaine conversation.">
                <ToggleField
                  label="Détecteur de fin de tour"
                  checked={draft.turn.turn_detector}
                  help="Petit modèle local qui juge, d'après les mots, si tu as fini ta phrase (anglais, LiveKit Model License, pas open source : désactivé par défaut). Sinon : silence seul."
                  onChange={(v) => set('turn', { turn_detector: v })}
                />
                <SliderField
                  label="Seuil du VAD"
                  value={draft.turn.vad_threshold}
                  min={0.1}
                  max={0.9}
                  step={0.05}
                  help="Plus haut = ignore les bruits ; trop haut = coupe les voix douces."
                  onChange={(v) => set('turn', { vad_threshold: v })}
                />
                <SliderField
                  label="Silence minimal"
                  value={draft.turn.min_silence}
                  min={0.1}
                  max={1.5}
                  step={0.05}
                  format={sec}
                  onChange={(v) => set('turn', { min_silence: v })}
                />
                <SliderField
                  label="Attente min après la phrase"
                  value={draft.turn.min_endpointing_delay}
                  min={0}
                  max={2}
                  step={0.05}
                  format={ms}
                  onChange={(v) => set('turn', { min_endpointing_delay: v })}
                />
                <SliderField
                  label="Attente max après la phrase"
                  value={draft.turn.max_endpointing_delay}
                  min={0.5}
                  max={6}
                  step={0.1}
                  format={sec}
                  help="Si le détecteur pense que tu n'as pas fini, l'agent attend jusqu'à cette limite."
                  onChange={(v) => set('turn', { max_endpointing_delay: v })}
                />
                <ToggleField
                  label="Me laisser l'interrompre"
                  checked={draft.turn.allow_interruptions}
                  onChange={(v) => set('turn', { allow_interruptions: v })}
                />
                <SliderField
                  label="Durée min pour interrompre"
                  value={draft.turn.min_interruption_duration}
                  min={0.1}
                  max={2}
                  step={0.05}
                  format={sec}
                  onChange={(v) => set('turn', { min_interruption_duration: v })}
                />
                <SliderField
                  label="Mots min pour interrompre"
                  value={draft.turn.min_interruption_words}
                  min={0}
                  max={6}
                  step={1}
                  format={int}
                  onChange={(v) => set('turn', { min_interruption_words: v })}
                />
              </Section>

              {defaults && (
                <Section title="Réinitialiser">
                  <Row>
                    <button
                      type="button"
                      className="text-destructive flex items-center gap-2 text-sm"
                      onClick={() => setDraft(structuredClone(defaults))}
                    >
                      <RotateCcw className="size-4" />
                      Revenir aux réglages par défaut
                    </button>
                  </Row>
                </Section>
              )}
            </div>

            <AnimatePresence>
              {dirty && (
                <motion.div
                  initial={{ y: 40, opacity: 0 }}
                  animate={{ y: 0, opacity: 1 }}
                  exit={{ y: 40, opacity: 0 }}
                  className="bg-background/95 absolute inset-x-3 bottom-3 flex items-center gap-2 rounded-3xl border border-white/10 px-4 py-3 shadow-2xl backdrop-blur-2xl"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold">Non enregistré</p>
                    <p className="text-muted-foreground truncate text-xs">
                      {later.length
                        ? `Prochaine conversation : ${later.join(', ')}`
                        : 'Appliqué dès la prochaine réponse'}
                    </p>
                  </div>
                  <Button variant="ghost" size="sm" onClick={() => setDraft(structuredClone(settings!))}>
                    Annuler
                  </Button>
                  <Button size="sm" onClick={onSave} disabled={saving} className="rounded-full">
                    {saving ? '…' : 'Enregistrer'}
                  </Button>
                </motion.div>
              )}
            </AnimatePresence>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
