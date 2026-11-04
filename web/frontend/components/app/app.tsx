'use client';

import { useEffect, useMemo, useState } from 'react';
import { TokenSource } from 'livekit-client';
import { useSession } from '@livekit/components-react';
import { WarningIcon } from '@phosphor-icons/react/dist/ssr';
import { AgentSessionProvider } from '@/components/agents-ui/agent-session-provider';
import { StartAudioButton } from '@/components/agents-ui/start-audio-button';
import { SettingsPanel } from '@/components/app/settings-panel';
import { TopBar } from '@/components/app/top-bar';
import { ViewController } from '@/components/app/view-controller';
import { Toaster } from '@/components/ui/sonner';
import { useAgentErrors } from '@/hooks/useAgentErrors';
import { useDebugMode } from '@/hooks/useDebug';
import { SettingsProvider, THEME_COLORS, useSettings } from '@/lib/settings';

const IN_DEVELOPMENT = process.env.NODE_ENV !== 'production';

function AppSetup() {
  useDebugMode({ enabled: IN_DEVELOPMENT });
  useAgentErrors();
  const { settings } = useSettings();

  // The color chosen in the settings becomes the accent of the whole interface.
  useEffect(() => {
    if (settings) {
      document.documentElement.style.setProperty('--primary', THEME_COLORS[settings.ui.theme]);
    }
  }, [settings]);

  return null;
}

interface AppProps {
  tokenServerId?: string;
  tokenEndpoint: string;
  agentName?: string;
  isVideoInputSupported: boolean;
}

export function App({ tokenServerId, tokenEndpoint, agentName, isVideoInputSupported }: AppProps) {
  const tokenSource = useMemo(
    () =>
      tokenServerId
        ? TokenSource.developmentTokenServer(tokenServerId)
        : TokenSource.endpoint(tokenEndpoint),
    [tokenServerId, tokenEndpoint]
  );

  const session = useSession(tokenSource, agentName ? { agentName } : undefined);
  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <SettingsProvider>
      <AgentSessionProvider session={session}>
        <AppSetup />
        <TopBar onOpenSettings={() => setSettingsOpen(true)} />
        <main className="grid h-svh w-full grid-cols-1 content-center justify-items-center">
          <ViewController isVideoInputSupported={isVideoInputSupported} />
        </main>
        <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} />
        <StartAudioButton label="Activer le son" />
      <Toaster
        icons={{
          warning: <WarningIcon weight="bold" />,
        }}
        position="top-center"
        className="toaster group"
        style={
          {
            '--normal-bg': 'var(--popover)',
            '--normal-text': 'var(--popover-foreground)',
            '--normal-border': 'var(--border)',
          } as React.CSSProperties
        }
        />
      </AgentSessionProvider>
    </SettingsProvider>
  );
}
