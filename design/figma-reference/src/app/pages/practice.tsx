export function PracticePage() {
  return (
    <div className="min-h-screen">
      <div className="max-w-6xl mx-auto px-8 py-16">
        {/* Header */}
        <div className="mb-16">
          <h1 
            className="mb-4"
            style={{
              fontSize: '2rem',
              lineHeight: '1.1',
              fontWeight: '500',
              letterSpacing: '-0.03em',
              color: '#F5F5F5'
            }}
          >
            Practice
          </h1>
          <p 
            style={{
              fontSize: '1rem',
              lineHeight: '1.6',
              fontWeight: '400',
              color: 'rgba(245, 245, 245, 0.55)',
              letterSpacing: '-0.01em'
            }}
          >
            Structured tools to build real musicianship. Each module targets a different skill.
          </p>
        </div>
        
        {/* Modules Grid */}
        <div className="grid md:grid-cols-2 gap-6">
          <ModuleCard
            title="Jam Tracks"
            features={[
              'Choose key, tempo, and style',
              'Blues, rock, funk, jazz presets',
              'Adjustable tempo from 60-200 BPM',
              'Loop any section for focused practice',
            ]}
            preview="12-Bar Blues in A"
            previewContent={`| A7    | A7    | A7    | A7    |
| D7    | D7    | A7    | A7    |
| E7    | D7    | A7    | E7    |`}
          >
            Play along with backing tracks in any key and style. Drums, bass, and rhythm guitar — you fill in the lead.
          </ModuleCard>
          
          <ModuleCard
            title="Scale Trainer"
            features={[
              'Major, minor, pentatonic, blues, modes',
              'Fretboard visualization',
              'Ascending, descending, random',
              'Metronome with gradual tempo increase',
            ]}
            preview="A Minor Pentatonic - Position 1"
            previewContent={`e|-----5--8-----|
B|-----5--8-----|
G|-----5--7-----|
D|-----5--7-----|
A|-----5--7-----|
E|-----5--8-----|`}
          >
            Learn and drill scales across the fretboard. Visual patterns, audio playback, and progressive difficulty.
          </ModuleCard>
          
          <ModuleCard
            title="Chord Trainer"
            features={[
              'Open, barre, and jazz voicings',
              'Speed drills with timer',
              'Random flash cards',
              'Progress tracking over time',
            ]}
            preview="Quick Change Drill"
            previewContent={`G → C → D → Em
| 4 | 4 | 4 | 4 |
| 3 | 3 | 3 | 3 |
| 2 | 2 | 2 | 2 |`}
          >
            Build chord vocabulary and smooth transitions. Flash cards, timed challenges, and voicing variations.
          </ModuleCard>
          
          <ModuleCard
            title="Progression Looper"
            features={[
              'I-V-vi-IV, 12-bar blues, ii-V-I',
              'Any key, any tempo',
              'Visual chord countdown',
              'Export as jam track',
            ]}
            preview="I-V-vi-IV in G"
            previewContent={`| G     | D     | Em    | C     |
| 4     | 3     | 2     | 1     |
[Loop repeating...]`}
          >
            Loop common chord progressions with a metronome click. Practice strumming, fingerpicking, and improvising over changes.
          </ModuleCard>
        </div>
      </div>
    </div>
  );
}

interface ModuleCardProps {
  title: string;
  children: React.ReactNode;
  features: string[];
  preview: string;
  previewContent: string;
}

function ModuleCard({ title, children, features, preview, previewContent }: ModuleCardProps) {
  return (
    <div className="p-6 border border-white/[0.08] hover:border-white/15 transition-colors">
      {/* Title and Badge */}
      <div className="flex items-center gap-3 mb-4">
        <h3 
          style={{
            fontSize: '1.125rem',
            fontWeight: '500',
            letterSpacing: '-0.02em',
            color: '#F5F5F5'
          }}
        >
          {title}
        </h3>
        <span 
          style={{
            fontSize: '0.6875rem',
            color: 'rgba(245, 245, 245, 0.35)',
            fontWeight: '400'
          }}
        >
          Coming Soon
        </span>
      </div>
      
      {/* Description */}
      <p 
        className="mb-4"
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
      
      {/* Features */}
      <ul className="mb-5 space-y-2">
        {features.map((feature, i) => (
          <li key={i} className="flex items-start gap-2.5">
            <span style={{ color: '#D4691F', fontSize: '0.5rem', marginTop: '0.5rem', opacity: 0.7 }}>●</span>
            <span 
              style={{
                fontSize: '0.875rem',
                color: 'rgba(245, 245, 245, 0.5)',
                fontWeight: '400',
                letterSpacing: '-0.01em'
              }}
            >
              {feature}
            </span>
          </li>
        ))}
      </ul>
      
      {/* Preview Box */}
      <div className="border border-white/[0.08] bg-black/30 p-4">
        <div 
          className="mb-2"
          style={{
            fontSize: '0.6875rem',
            textTransform: 'uppercase',
            letterSpacing: '0.05em',
            color: 'rgba(245, 245, 245, 0.4)',
            fontWeight: '500'
          }}
        >
          Preview: {preview}
        </div>
        <pre 
          style={{
            fontFamily: 'monospace',
            fontSize: '0.8125rem',
            color: 'rgba(245, 245, 245, 0.5)',
            lineHeight: '1.6'
          }}
        >
          {previewContent}
        </pre>
      </div>
    </div>
  );
}
