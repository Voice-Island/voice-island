'use client';

import { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { RoomEvent } from 'livekit-client';
import { useSessionContext } from '@livekit/components-react';
import { Timer } from 'lucide-react';

// Sent by the agent (web/agent/agent.py) with each answer, on this topic.
const METRICS_TOPIC = 'voice.metrics';

type TurnMetrics = {
  eou_ms: number;
  stt_ms: number;
  llm_ms?: number;
  tts_ms: number;
  total_ms: number;
  engines?: { llm: string; stt: string; tts: string };
};

function Chip({ label, value }: { label: string; value?: number }) {
  return (
    <span className="flex items-baseline gap-1">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono tabular-nums">{value === undefined ? '…' : Math.round(value)}</span>
    </span>
  );
}

/** Timings of the last answer: end of speech, LLM, voice, total (ms). */
export function TurnMetricsBar() {
  const { room } = useSessionContext();
  const [last, setLast] = useState<TurnMetrics | null>(null);

  useEffect(() => {
    if (!room) return;
    const decoder = new TextDecoder();
    const onData = (payload: Uint8Array, _p: unknown, _k: unknown, topic?: string) => {
      if (topic !== METRICS_TOPIC) return;
      try {
        setLast(JSON.parse(decoder.decode(payload)));
      } catch {
        // unreadable message: ignored
      }
    };
    room.on(RoomEvent.DataReceived, onData);
    return () => {
      room.off(RoomEvent.DataReceived, onData);
    };
  }, [room]);

  return (
    <AnimatePresence>
      {last && (
        <motion.div
          key="metrics"
          initial={{ opacity: 0, y: -6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0 }}
          className="bg-background/60 flex flex-wrap items-center justify-center gap-x-3 gap-y-1 rounded-full border border-white/10 px-4 py-1.5 text-xs shadow-sm backdrop-blur-xl"
          title={last.engines ? `${last.engines.llm} · ${last.engines.stt} · ${last.engines.tts}` : undefined}
        >
          <Timer className="text-muted-foreground size-3.5" />
          <Chip label="fin de tour" value={last.eou_ms} />
          <Chip label="LLM" value={last.llm_ms} />
          <Chip label="voix" value={last.tts_ms} />
          <span className="flex items-baseline gap-1 font-semibold">
            <span>total</span>
            <span className="font-mono tabular-nums">{Math.round(last.total_ms)} ms</span>
          </span>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
