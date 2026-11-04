import { AudioLines, Lock, Mic } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface WelcomeViewProps {
  startButtonText: string;
  onStartCall: () => void;
}

export const WelcomeView = ({
  startButtonText,
  onStartCall,
  ref,
}: React.ComponentProps<'div'> & WelcomeViewProps) => {
  return (
    <div ref={ref} className="w-full px-4">
      <section className="mx-auto flex w-full max-w-md flex-col items-center rounded-[2rem] border border-white/10 bg-background/60 px-8 py-10 text-center shadow-xl backdrop-blur-2xl">
        <div className="bg-primary/15 mb-5 flex size-20 items-center justify-center rounded-full">
          <AudioLines className="text-primary size-10" />
        </div>
        <h1 className="text-2xl font-semibold">Assistant vocal</h1>
        <p className="text-muted-foreground mt-2 leading-relaxed">
          Parle-lui en anglais. Transcription, réflexion et voix tournent sur ta machine.
        </p>
        <Button size="lg" onClick={onStartCall} className="mt-7 h-12 w-full gap-2 rounded-full text-base">
          <Mic className="size-5" />
          {startButtonText}
        </Button>
        <p className="text-muted-foreground mt-5 flex items-center gap-1.5 text-xs">
          <Lock className="size-3.5" />
          Aucune donnée envoyée dans le cloud
        </p>
      </section>
    </div>
  );
};
