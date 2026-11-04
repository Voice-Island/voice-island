'use client';

import { AudioLines, Moon, Settings, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';
import { useSessionContext } from '@livekit/components-react';
import { TurnMetricsBar } from '@/components/app/turn-metrics';
import { useSettings } from '@/lib/settings';

/** Glass top bar: name, measurements of the last answer, theme, settings. */
export function TopBar({ onOpenSettings }: { onOpenSettings: () => void }) {
  const { isConnected } = useSessionContext();
  const { settings } = useSettings();
  const { resolvedTheme, setTheme } = useTheme();

  return (
    <header className="pointer-events-none fixed inset-x-0 top-0 z-50 flex flex-col items-center gap-2 px-3 pt-3 md:px-6 md:pt-5">
      <div className="pointer-events-auto flex w-full max-w-3xl items-center justify-between gap-3 rounded-full border border-white/10 bg-background/60 py-1.5 pr-1.5 pl-4 shadow-sm backdrop-blur-xl">
        <div className="flex items-center gap-2 font-semibold">
          <AudioLines className="text-primary size-5" />
          <span>Voice</span>
          <span className="text-muted-foreground hidden text-xs font-normal sm:inline">· 100 % local</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
            aria-label="Changer de thème"
            className="hover:bg-muted flex size-9 items-center justify-center rounded-full transition-colors"
          >
            <Sun className="hidden size-5 dark:block" />
            <Moon className="size-5 dark:hidden" />
          </button>
          <button
            type="button"
            onClick={onOpenSettings}
            aria-label="Réglages"
            className="hover:bg-muted flex size-9 items-center justify-center rounded-full transition-colors"
          >
            <Settings className="size-5" />
          </button>
        </div>
      </div>
      {isConnected && settings?.ui.show_metrics && (
        <div className="pointer-events-auto">
          <TurnMetricsBar />
        </div>
      )}
    </header>
  );
}
