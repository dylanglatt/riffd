import { useState } from 'react';
import { Search, Music, BarChart3, Activity, Target } from 'lucide-react';

type Section = 'chords' | 'scales' | 'progressions' | 'keys';

export function StudioPage() {
  const [activeSection, setActiveSection] = useState<Section>('chords');
  
  return (
    <div className="min-h-screen">
      <div className="max-w-7xl mx-auto px-8 py-16">
        {/* Search Bar */}
        <div className="max-w-xl mx-auto mb-16">
          <div className="relative">
            <Search 
              className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5"
              style={{ color: 'rgba(245, 245, 245, 0.3)' }}
            />
            <input
              type="text"
              placeholder="Search chords, scales, progressions..."
              className="w-full pl-12 pr-4 py-3.5 bg-transparent border border-white/[0.15] text-white outline-none transition-colors focus:border-white/30"
              style={{
                fontSize: '0.9375rem',
                fontWeight: '400',
                letterSpacing: '-0.01em'
              }}
            />
          </div>
        </div>
        
        {/* Two-Column Layout */}
        <div className="flex gap-12">
          {/* Sidebar */}
          <aside className="w-48 flex-shrink-0">
            <div className="space-y-2">
              <SidebarLink
                icon={Music}
                label="Chords"
                active={activeSection === 'chords'}
                onClick={() => setActiveSection('chords')}
              />
              <SidebarLink
                icon={BarChart3}
                label="Scales"
                active={activeSection === 'scales'}
                onClick={() => setActiveSection('scales')}
              />
              <SidebarLink
                icon={Activity}
                label="Progressions"
                active={activeSection === 'progressions'}
                onClick={() => setActiveSection('progressions')}
              />
              <SidebarLink
                icon={Target}
                label="Keys"
                active={activeSection === 'keys'}
                onClick={() => setActiveSection('keys')}
              />
            </div>
          </aside>
          
          {/* Main Content */}
          <main className="flex-1">
            {activeSection === 'chords' && <ChordsSection />}
            {activeSection === 'scales' && <ScalesSection />}
            {activeSection === 'progressions' && <ProgressionsSection />}
            {activeSection === 'keys' && <KeysSection />}
          </main>
        </div>
      </div>
    </div>
  );
}

interface SidebarLinkProps {
  icon: any;
  label: string;
  active: boolean;
  onClick: () => void;
}

function SidebarLink({ icon: Icon, label, active, onClick }: SidebarLinkProps) {
  return (
    <button
      onClick={onClick}
      className="w-full text-left py-2 px-3 transition-colors relative"
      style={{
        fontSize: '0.9375rem',
        fontWeight: '400',
        color: active ? '#F5F5F5' : 'rgba(245, 245, 245, 0.5)',
        letterSpacing: '-0.01em',
        background: 'transparent',
        border: 'none',
        cursor: 'pointer'
      }}
    >
      {active && (
        <div 
          className="absolute left-0 top-0 bottom-0 w-px"
          style={{ backgroundColor: '#D4691F', opacity: 0.7 }}
        />
      )}
      {label}
    </button>
  );
}

function ChordsSection() {
  const chords = [
    { name: 'C Major', formula: 'C - E - G', desc: 'The most fundamental major triad. Perfect for beginners learning their first chord shapes.', difficulty: 'Beginner' },
    { name: 'G Major', formula: 'G - B - D', desc: 'One of the most common open chords. Found in countless songs across all genres.', difficulty: 'Beginner' },
    { name: 'D Major', formula: 'D - F# - A', desc: 'Bright and uplifting sound. Often used in folk and country music.', difficulty: 'Beginner' },
    { name: 'A Minor', formula: 'A - C - E', desc: 'The relative minor of C Major. Dark and melancholic character.', difficulty: 'Beginner' },
    { name: 'E Minor', formula: 'E - G - B', desc: 'Easiest chord to play. Rich, full sound with open strings.', difficulty: 'Beginner' },
    { name: 'F Major', formula: 'F - A - C', desc: 'First barre chord many guitarists learn. Essential for key changes.', difficulty: 'Intermediate' },
  ];
  
  return (
    <div>
      <div className="grid grid-cols-3 gap-4">
        {chords.map((chord, i) => (
          <TheoryCard key={i} {...chord} />
        ))}
      </div>
    </div>
  );
}

function ScalesSection() {
  const scales = [
    { name: 'C Major Scale', formula: '1 - 2 - 3 - 4 - 5 - 6 - 7', desc: 'The foundational scale in Western music. Happy and bright character.', difficulty: 'Beginner' },
    { name: 'A Natural Minor', formula: '1 - 2 - b3 - 4 - 5 - b6 - b7', desc: 'Relative minor of C Major. Dark and somber mood.', difficulty: 'Beginner' },
    { name: 'A Minor Pentatonic', formula: '1 - b3 - 4 - 5 - b7', desc: 'Most popular scale for rock and blues soloing. Easy to improvise with.', difficulty: 'Beginner' },
    { name: 'E Blues Scale', formula: '1 - b3 - 4 - b5 - 5 - b7', desc: 'Pentatonic minor with added blue note. Essential for blues guitar.', difficulty: 'Beginner' },
  ];
  
  return (
    <div>
      <div className="grid grid-cols-3 gap-4">
        {scales.map((scale, i) => (
          <TheoryCard key={i} {...scale} />
        ))}
      </div>
    </div>
  );
}

function ProgressionsSection() {
  const progressions = [
    { name: 'I - V - vi - IV', formula: 'C - G - Am - F', desc: 'The most popular progression in modern music. Used in thousands of hit songs.', difficulty: 'Beginner' },
    { name: 'I - IV - V', formula: 'C - F - G', desc: 'Classic rock progression. Simple and powerful.', difficulty: 'Beginner' },
    { name: '12-Bar Blues', formula: 'I - I - I - I - IV - IV - I - I - V - IV - I - V', desc: 'Foundation of blues music. Essential pattern for all blues players.', difficulty: 'Intermediate' },
    { name: 'ii - V - I', formula: 'Dm7 - G7 - Cmaj7', desc: 'Most common jazz cadence. Creates strong resolution.', difficulty: 'Intermediate' },
  ];
  
  return (
    <div>
      <div className="grid grid-cols-3 gap-4">
        {progressions.map((prog, i) => (
          <TheoryCard key={i} {...prog} />
        ))}
      </div>
    </div>
  );
}

function KeysSection() {
  const keys = [
    { name: 'C Major', formula: 'C - D - E - F - G - A - B', desc: 'No sharps or flats. Perfect starting point for theory.', difficulty: 'Beginner' },
    { name: 'G Major', formula: 'G - A - B - C - D - E - F#', desc: 'One sharp (F#). Very common in folk and country.', difficulty: 'Beginner' },
    { name: 'A Minor', formula: 'A - B - C - D - E - F - G', desc: 'No sharps or flats. Relative minor of C Major.', difficulty: 'Beginner' },
    { name: 'E Minor', formula: 'E - F# - G - A - B - C - D', desc: 'One sharp (F#). Relative minor of G Major.', difficulty: 'Beginner' },
  ];
  
  return (
    <div>
      <div className="grid grid-cols-3 gap-4">
        {keys.map((key, i) => (
          <TheoryCard key={i} {...key} />
        ))}
      </div>
    </div>
  );
}

interface TheoryCardProps {
  name: string;
  formula: string;
  desc: string;
  difficulty: string;
}

function TheoryCard({ name, formula, desc, difficulty }: TheoryCardProps) {
  return (
    <div className="p-5 border border-white/[0.08] hover:border-white/20 transition-colors">
      <h4 
        className="mb-2"
        style={{
          fontSize: '0.9375rem',
          fontWeight: '500',
          letterSpacing: '-0.01em',
          color: '#F5F5F5'
        }}
      >
        {name}
      </h4>
      
      <div 
        className="mb-3"
        style={{
          fontFamily: 'monospace',
          fontSize: '0.8125rem',
          color: '#D4691F',
          opacity: 0.8
        }}
      >
        {formula}
      </div>
      
      <p 
        className="mb-3"
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
      
      <span 
        style={{
          fontSize: '0.75rem',
          color: 'rgba(245, 245, 245, 0.35)',
          fontWeight: '400'
        }}
      >
        {difficulty}
      </span>
    </div>
  );
}
