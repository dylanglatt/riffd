export function AboutPage() {
  return (
    <div className="min-h-screen">
      <div className="max-w-4xl mx-auto px-8 py-16">
        {/* Hero */}
        <section className="text-center mb-32">
          <h1 
            className="mb-6"
            style={{
              fontSize: '3rem',
              lineHeight: '1.1',
              fontWeight: '500',
              letterSpacing: '-0.03em',
              color: '#F5F5F5'
            }}
          >
            How Riffd works
          </h1>
          
          <p 
            className="max-w-2xl mx-auto"
            style={{
              fontSize: '1rem',
              lineHeight: '1.7',
              fontWeight: '400',
              color: 'rgba(245, 245, 245, 0.55)',
              letterSpacing: '-0.01em'
            }}
          >
            Riffd uses AI audio analysis to break songs into individual parts, detect musical properties, 
            and give you the tools to learn and play along. No sheet music required.
          </p>
        </section>
        
        {/* The Pipeline */}
        <section className="mb-32">
          <h2 
            className="text-center mb-12"
            style={{
              fontSize: '0.8125rem',
              textTransform: 'uppercase',
              letterSpacing: '0.1em',
              color: 'rgba(245, 245, 245, 0.4)',
              fontWeight: '500'
            }}
          >
            The Pipeline
          </h2>
          
          <div className="grid grid-cols-4 gap-8">
            <PipelineStep number={1} title="Input">
              Search for any song or upload an audio file directly.
            </PipelineStep>
            <PipelineStep number={2} title="Separate">
              AI isolates vocals, guitar, bass, drums, piano, and other instruments.
            </PipelineStep>
            <PipelineStep number={3} title="Analyze">
              Detect key, BPM, chord progression, and generate tablature from each stem.
            </PipelineStep>
            <PipelineStep number={4} title="Explore">
              Mix stems, read tabs, loop sections, transpose, and learn at your own pace.
            </PipelineStep>
          </div>
        </section>
        
        {/* Technology */}
        <section className="mb-32">
          <h2 
            className="text-center mb-12"
            style={{
              fontSize: '0.8125rem',
              textTransform: 'uppercase',
              letterSpacing: '0.1em',
              color: 'rgba(245, 245, 245, 0.4)',
              fontWeight: '500'
            }}
          >
            Technology
          </h2>
          
          <div className="grid grid-cols-3 gap-5">
            <TechCard
              name="Demucs"
              role="Stem Separation"
              desc="Meta's state-of-the-art transformer model for source separation. Isolates vocals, drums, bass, and other instruments with high fidelity."
            />
            <TechCard
              name="Basic Pitch"
              role="Note Detection"
              desc="Spotify's neural network for pitch detection. Converts audio to MIDI with accurate note onset and duration."
            />
            <TechCard
              name="Krumhansl-Schmuckler"
              role="Key Detection"
              desc="Pitch-class profiling algorithm that determines the tonal center and mode of a piece."
            />
            <TechCard
              name="Web Audio API"
              role="Playback Engine"
              desc="Browser-native audio processing. Enables real-time mixing, looping, and transposition without server roundtrips."
            />
            <TechCard
              name="Diatonic Template Matching"
              role="Chord Analysis"
              desc="Windowed pitch-class histogram analysis matched against diatonic chord templates."
            />
            <TechCard
              name="Flask + Vanilla JS"
              role="Application Stack"
              desc="Lightweight Python backend with zero-dependency frontend. Fast, simple, and maintainable."
            />
          </div>
        </section>
        
        {/* Principles */}
        <section className="mb-20">
          <h2 
            className="text-center mb-12"
            style={{
              fontSize: '0.8125rem',
              textTransform: 'uppercase',
              letterSpacing: '0.1em',
              color: 'rgba(245, 245, 245, 0.4)',
              fontWeight: '500'
            }}
          >
            Principles
          </h2>
          
          <div className="max-w-2xl mx-auto space-y-8">
            <PrincipleCard title="Built for musicians">
              Every feature is designed for people who play instruments. Not a tech demo — a tool you actually use in your practice room.
            </PrincipleCard>
            <PrincipleCard title="Honest about limitations">
              AI analysis isn't perfect. We show confidence levels and suppress low-quality results rather than guessing.
            </PrincipleCard>
            <PrincipleCard title="Ship and iterate">
              Core features are live. Tab accuracy, drum detection, and chord analysis are actively improving with every update.
            </PrincipleCard>
          </div>
        </section>
      </div>
    </div>
  );
}

interface PipelineStepProps {
  number: number;
  title: string;
  children: React.ReactNode;
}

function PipelineStep({ number, title, children }: PipelineStepProps) {
  return (
    <div className="text-center">
      <div 
        className="w-10 h-10 rounded-full mx-auto mb-4 border border-white/[0.15] flex items-center justify-center"
        style={{
          fontSize: '0.9375rem',
          fontWeight: '500',
          color: 'rgba(245, 245, 245, 0.7)'
        }}
      >
        {number}
      </div>
      <h3 
        className="mb-2"
        style={{
          fontSize: '0.9375rem',
          fontWeight: '500',
          letterSpacing: '-0.01em',
          color: '#F5F5F5'
        }}
      >
        {title}
      </h3>
      <p 
        style={{
          fontSize: '0.875rem',
          lineHeight: '1.5',
          fontWeight: '400',
          color: 'rgba(245, 245, 245, 0.5)',
          letterSpacing: '-0.01em'
        }}
      >
        {children}
      </p>
    </div>
  );
}

interface TechCardProps {
  name: string;
  role: string;
  desc: string;
}

function TechCard({ name, role, desc }: TechCardProps) {
  return (
    <div className="p-5 border border-white/[0.08]">
      <h4 
        className="mb-1"
        style={{
          fontSize: '0.9375rem',
          fontWeight: '500',
          letterSpacing: '-0.01em',
          color: '#F5F5F5'
        }}
      >
        {name}
      </h4>
      <p 
        className="mb-2"
        style={{
          fontSize: '0.8125rem',
          color: '#D4691F',
          opacity: 0.8,
          fontWeight: '400',
          letterSpacing: '-0.01em'
        }}
      >
        {role}
      </p>
      <p 
        style={{
          fontSize: '0.875rem',
          lineHeight: '1.5',
          fontWeight: '400',
          color: 'rgba(245, 245, 245, 0.5)',
          letterSpacing: '-0.01em'
        }}
      >
        {desc}
      </p>
    </div>
  );
}

interface PrincipleCardProps {
  title: string;
  children: React.ReactNode;
}

function PrincipleCard({ title, children }: PrincipleCardProps) {
  return (
    <div>
      <h4 
        className="mb-2"
        style={{
          fontSize: '1rem',
          fontWeight: '500',
          letterSpacing: '-0.01em',
          color: '#F5F5F5'
        }}
      >
        {title}
      </h4>
      <p 
        style={{
          fontSize: '0.9375rem',
          lineHeight: '1.6',
          fontWeight: '400',
          color: 'rgba(245, 245, 245, 0.55)',
          letterSpacing: '-0.01em'
        }}
      >
        {children}
      </p>
    </div>
  );
}
