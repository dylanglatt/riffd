import { useState, useEffect } from 'react';
import { Search, Upload, Layers, BarChart3, Play, Music } from 'lucide-react';

export function DecomposePage() {
  const [view, setView] = useState<'search' | 'processing' | 'results'>('search');
  
  return (
    <div className="min-h-screen">
      {view === 'search' && <SearchView onSelect={() => setView('processing')} />}
      {view === 'processing' && <ProcessingView onComplete={() => setView('results')} />}
      {view === 'results' && <ResultsView onNewSong={() => setView('search')} />}
    </div>
  );
}

function SearchView({ onSelect }: { onSelect: () => void }) {
  return (
    <div className="max-w-2xl mx-auto px-8 pt-32 pb-40">
      {/* Header */}
      <div className="text-center mb-20">
        <h1 
          className="mb-6"
          style={{
            fontSize: '4rem',
            lineHeight: '1.05',
            fontWeight: '500',
            letterSpacing: '-0.04em',
            color: '#F5F5F5'
          }}
        >
          Decompose any song.
        </h1>
        
        <p 
          style={{
            fontSize: '1.125rem',
            lineHeight: '1.65',
            fontWeight: '400',
            color: 'rgba(245, 245, 245, 0.55)',
            letterSpacing: '-0.01em'
          }}
        >
          Separate stems, uncover structure, and play along in seconds.
        </p>
      </div>
      
      {/* Search Input */}
      <div className="mb-8">
        <div className="relative">
          <Search 
            className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5"
            style={{ color: 'rgba(245, 245, 245, 0.3)' }}
          />
          <input
            type="text"
            placeholder="Search any song or artist..."
            className="w-full pl-12 pr-4 py-4 bg-transparent border border-white/[0.15] text-white outline-none transition-colors focus:border-white/30"
            style={{
              fontSize: '0.9375rem',
              fontWeight: '400',
              letterSpacing: '-0.01em'
            }}
          />
        </div>
      </div>
      
      {/* Divider */}
      <div className="flex items-center gap-4 my-12">
        <div className="flex-1 h-px bg-white/[0.08]" />
        <span 
          style={{
            fontSize: '0.8125rem',
            color: 'rgba(245, 245, 245, 0.35)',
            fontWeight: '400'
          }}
        >
          or
        </span>
        <div className="flex-1 h-px bg-white/[0.08]" />
      </div>
      
      {/* Upload Area */}
      <div 
        className="border-2 border-dashed border-white/[0.15] p-16 text-center mb-20 cursor-pointer hover:border-white/25 transition-colors"
        onClick={onSelect}
      >
        <Upload 
          className="w-6 h-6 mx-auto mb-3"
          style={{ color: 'rgba(245, 245, 245, 0.35)' }}
        />
        <p 
          style={{
            fontSize: '0.9375rem',
            color: 'rgba(245, 245, 245, 0.5)',
            fontWeight: '400',
            letterSpacing: '-0.01em'
          }}
        >
          Drop a file
        </p>
      </div>
      
      {/* What Riffd does */}
      <div>
        <h2 
          className="mb-12"
          style={{
            fontSize: '0.9375rem',
            fontWeight: '500',
            letterSpacing: '-0.01em',
            color: 'rgba(245, 245, 245, 0.7)'
          }}
        >
          What Riffd does
        </h2>
        
        <div className="grid grid-cols-4 gap-8">
          <CapabilityCard
            icon={Layers}
            title="Separate stems"
            description="Vocals, drums, bass, guitar, keys — isolated from any track."
          />
          <CapabilityCard
            icon={BarChart3}
            title="Read the structure"
            description="Key, tempo, chord progression, and harmonic analysis."
          />
          <CapabilityCard
            icon={Play}
            title="Play it back"
            description="Solo any stem, loop sections, transpose to any key."
          />
          <CapabilityCard
            icon={Music}
            title="Learn faster"
            description="Auto-generated tabs and MIDI for every instrument."
          />
        </div>
      </div>
    </div>
  );
}

function ProcessingView({ onComplete }: { onComplete: () => void }) {
  // Auto-advance after 3 seconds
  useEffect(() => {
    const timer = setTimeout(onComplete, 3000);
    return () => clearTimeout(timer);
  }, [onComplete]);
  
  return (
    <div className="min-h-[80vh] flex flex-col items-center justify-center px-8">
      {/* Album Art */}
      <div className="mb-8">
        <div 
          className="w-[180px] h-[180px] border border-white/[0.08] bg-[#0D0D0D]"
        />
      </div>
      
      {/* Song Info */}
      <h2 
        className="mb-2"
        style={{
          fontSize: '1.5rem',
          fontWeight: '500',
          letterSpacing: '-0.02em',
          color: '#F5F5F5'
        }}
      >
        Song Title
      </h2>
      <p 
        className="mb-6"
        style={{
          fontSize: '0.9375rem',
          color: 'rgba(245, 245, 245, 0.5)',
          fontWeight: '400',
          letterSpacing: '-0.01em'
        }}
      >
        Artist
      </p>
      
      {/* Metadata Badges */}
      <div className="flex items-center gap-3 mb-12">
        <InfoBadge label="GENRE" value="Rock" />
        <InfoBadge label="BPM" value="120" />
        <InfoBadge label="KEY" value="Am" />
      </div>
      
      {/* Waveform Progress */}
      <div className="w-full max-w-md mb-4">
        <div className="border border-white/[0.08] p-6">
          <div className="flex items-center justify-center gap-[1px] h-20">
            {Array.from({ length: 60 }).map((_, i) => (
              <div
                key={i}
                className="w-[2px]"
                style={{
                  height: `${Math.random() * 100}%`,
                  backgroundColor: i % 15 === 0 ? '#D4691F' : 'rgba(245, 245, 245, 0.3)',
                  opacity: i % 15 === 0 ? 0.7 : undefined
                }}
              />
            ))}
          </div>
        </div>
      </div>
      
      <p 
        style={{
          fontSize: '0.875rem',
          color: 'rgba(245, 245, 245, 0.4)',
          fontWeight: '400',
          letterSpacing: '-0.01em'
        }}
      >
        Separating instruments...
      </p>
    </div>
  );
}

function ResultsView({ onNewSong }: { onNewSong: () => void }) {
  const [activeTab, setActiveTab] = useState<'mix' | 'tab' | 'lyrics'>('mix');
  
  return (
    <div className="max-w-5xl mx-auto px-8 py-16">
      {/* Results Container */}
      <div className="border border-white/[0.08]">
        {/* Header */}
        <div className="border-b border-white/[0.08] p-6 flex items-center gap-4">
          <div className="w-[52px] h-[52px] border border-white/[0.08] bg-[#0D0D0D] flex-shrink-0" />
          <div>
            <h3 
              style={{
                fontSize: '1rem',
                fontWeight: '500',
                letterSpacing: '-0.01em',
                color: '#F5F5F5',
                marginBottom: '0.25rem'
              }}
            >
              Song Title
            </h3>
            <p 
              style={{
                fontSize: '0.875rem',
                color: 'rgba(245, 245, 245, 0.5)',
                fontWeight: '400',
                letterSpacing: '-0.01em'
              }}
            >
              Artist
            </p>
          </div>
        </div>
        
        {/* Metadata Row */}
        <div className="border-b border-white/[0.08] p-6 flex items-center gap-3">
          <MetadataBadge label="GENRE" value="Rock" />
          <MetadataBadge label="BPM" value="120" />
          <MetadataBadge label="KEY" value="A Minor" />
          <MetadataBadge label="PROGRESSION" value="i - VI - III - VII" />
        </div>
        
        {/* Tabs */}
        <div className="border-b border-white/[0.08] px-6 flex items-center gap-8">
          <TabButton 
            active={activeTab === 'mix'} 
            onClick={() => setActiveTab('mix')}
          >
            Mix
          </TabButton>
          <TabButton 
            active={activeTab === 'tab'} 
            onClick={() => setActiveTab('tab')}
          >
            Tab
          </TabButton>
          <TabButton 
            active={activeTab === 'lyrics'} 
            onClick={() => setActiveTab('lyrics')}
          >
            Lyrics
          </TabButton>
        </div>
        
        {/* Tab Content */}
        <div className="p-6">
          {activeTab === 'mix' && <MixPanel />}
          {activeTab === 'tab' && <TabPanel />}
          {activeTab === 'lyrics' && <LyricsPanel />}
        </div>
        
        {/* Footer */}
        <div className="border-t border-white/[0.08] p-6 text-center">
          <button
            onClick={onNewSong}
            className="px-6 py-2.5 border border-white/[0.15] hover:border-white/30 transition-colors"
            style={{
              fontSize: '0.9375rem',
              fontWeight: '400',
              letterSpacing: '-0.01em',
              color: '#F5F5F5'
            }}
          >
            New Song
          </button>
        </div>
      </div>
    </div>
  );
}

function CapabilityCard({ icon: Icon, title, description }: { icon: any; title: string; description: string }) {
  return (
    <div>
      <Icon 
        className="w-5 h-5 mb-4"
        strokeWidth={1.5}
        style={{ color: '#D4691F', opacity: 0.85 }}
      />
      <h4 
        className="mb-2"
        style={{
          fontSize: '0.9375rem',
          fontWeight: '500',
          letterSpacing: '-0.01em',
          color: '#F5F5F5'
        }}
      >
        {title}
      </h4>
      <p 
        style={{
          fontSize: '0.875rem',
          lineHeight: '1.5',
          fontWeight: '400',
          color: 'rgba(245, 245, 245, 0.5)',
          letterSpacing: '-0.01em'
        }}
      >
        {description}
      </p>
    </div>
  );
}

function InfoBadge({ label, value }: { label: string; value: string }) {
  return (
    <div className="px-3 py-2 border border-white/[0.08]">
      <div 
        className="mb-1"
        style={{
          fontSize: '0.625rem',
          color: 'rgba(245, 245, 245, 0.4)',
          fontWeight: '500',
          letterSpacing: '0.05em'
        }}
      >
        {label}
      </div>
      <div 
        style={{
          fontSize: '0.875rem',
          fontWeight: '500',
          letterSpacing: '-0.01em',
          color: '#F5F5F5'
        }}
      >
        {value}
      </div>
    </div>
  );
}

function MetadataBadge({ label, value }: { label: string; value: string }) {
  return (
    <div className="px-3 py-1.5 border border-white/[0.08]">
      <span 
        style={{
          fontSize: '0.6875rem',
          color: 'rgba(245, 245, 245, 0.4)',
          fontWeight: '400',
          letterSpacing: '0.03em',
          marginRight: '0.5rem'
        }}
      >
        {label}
      </span>
      <span 
        style={{
          fontSize: '0.8125rem',
          fontWeight: '500',
          letterSpacing: '-0.01em',
          color: '#F5F5F5'
        }}
      >
        {value}
      </span>
    </div>
  );
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className="py-4 transition-colors relative"
      style={{
        fontSize: '0.9375rem',
        fontWeight: '400',
        letterSpacing: '-0.01em',
        color: active ? '#F5F5F5' : 'rgba(245, 245, 245, 0.5)',
        background: 'transparent',
        border: 'none',
        cursor: 'pointer'
      }}
    >
      {children}
      {active && (
        <div 
          className="absolute bottom-0 left-0 right-0 h-px"
          style={{ backgroundColor: '#D4691F', opacity: 0.7 }}
        />
      )}
    </button>
  );
}

function MixPanel() {
  return (
    <div>
      {/* Transport Bar */}
      <div className="mb-6 flex items-center gap-4">
        <button 
          className="w-12 h-12 flex items-center justify-center hover:bg-[#FAFAF8] transition-colors"
          style={{ backgroundColor: '#FAFAF9' }}
        >
          <Play className="w-5 h-5" style={{ color: '#0B0B0B' }} />
        </button>
        
        <button 
          className="w-10 h-10 border border-white/[0.15] flex items-center justify-center hover:border-white/30 transition-colors"
        >
          <div className="w-3 h-3 bg-white/70" />
        </button>
        
        <div className="flex-1 h-1 bg-white/[0.1] relative">
          <div className="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full" style={{ backgroundColor: '#D4691F', left: '30%' }} />
        </div>
        
        <span 
          style={{
            fontSize: '0.8125rem',
            color: 'rgba(245, 245, 245, 0.5)',
            fontFamily: 'monospace'
          }}
        >
          0:00 / 3:42
        </span>
      </div>
      
      {/* Channel Strips */}
      <div className="space-y-3 mb-6">
        <StemChannel name="Vocals" />
        <StemChannel name="Drums" />
        <StemChannel name="Bass" />
        <StemChannel name="Guitar" />
      </div>
      
      {/* Tools Row */}
      <div className="flex items-center gap-8 pt-4 border-t border-white/[0.08]">
        <div className="flex items-center gap-3">
          <span 
            style={{
              fontSize: '0.75rem',
              color: 'rgba(245, 245, 245, 0.4)',
              fontWeight: '500',
              letterSpacing: '0.05em'
            }}
          >
            LOOP
          </span>
          <input 
            type="text" 
            placeholder="0:00"
            className="w-16 px-2 py-1 bg-transparent border border-white/[0.15] text-center"
            style={{ fontSize: '0.8125rem', color: '#F5F5F5' }}
          />
          <span style={{ fontSize: '0.75rem', color: 'rgba(245, 245, 245, 0.3)' }}>to</span>
          <input 
            type="text" 
            placeholder="end"
            className="w-16 px-2 py-1 bg-transparent border border-white/[0.15] text-center"
            style={{ fontSize: '0.8125rem', color: '#F5F5F5' }}
          />
        </div>
        
        <div className="flex items-center gap-3">
          <span 
            style={{
              fontSize: '0.75rem',
              color: 'rgba(245, 245, 245, 0.4)',
              fontWeight: '500',
              letterSpacing: '0.05em'
            }}
          >
            KEY
          </span>
          <button className="w-7 h-7 border border-white/[0.15] flex items-center justify-center hover:border-white/30">
            <span style={{ fontSize: '0.875rem', color: '#F5F5F5' }}>−</span>
          </button>
          <span style={{ fontSize: '0.875rem', fontWeight: '500', color: '#F5F5F5' }}>0</span>
          <button className="w-7 h-7 border border-white/[0.15] flex items-center justify-center hover:border-white/30">
            <span style={{ fontSize: '0.875rem', color: '#F5F5F5' }}>+</span>
          </button>
        </div>
      </div>
    </div>
  );
}

function StemChannel({ name }: { name: string }) {
  return (
    <div className="grid gap-4 items-center" style={{ gridTemplateColumns: '100px 1fr auto auto' }}>
      <span 
        style={{
          fontSize: '0.875rem',
          fontWeight: '400',
          letterSpacing: '-0.01em',
          color: '#F5F5F5'
        }}
      >
        {name}
      </span>
      
      <div className="h-1 bg-white/[0.1] relative">
        <div className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 rounded-full" style={{ backgroundColor: '#D4691F', left: '60%' }} />
      </div>
      
      <button 
        className="w-6 h-6 border border-white/[0.15] flex items-center justify-center hover:border-white/30 transition-colors"
        style={{ fontSize: '0.6875rem', fontWeight: '500', color: 'rgba(245, 245, 245, 0.6)' }}
      >
        M
      </button>
      
      <button 
        className="w-6 h-6 border border-white/[0.15] flex items-center justify-center hover:border-white/30 transition-colors"
        style={{ fontSize: '0.6875rem', fontWeight: '500', color: 'rgba(245, 245, 245, 0.6)' }}
      >
        S
      </button>
    </div>
  );
}

function TabPanel() {
  return (
    <div className="relative min-h-[300px] flex items-center justify-center">
      <div 
        className="absolute inset-0 flex items-center justify-center"
        style={{ filter: 'blur(2px)', opacity: 0.3 }}
      >
        <pre 
          style={{
            fontFamily: 'monospace',
            fontSize: '0.8125rem',
            color: 'rgba(245, 245, 245, 0.5)',
            lineHeight: '1.5'
          }}
        >
{`e|--0--2--3--5--7--|
B|--0--1--3--5--7--|
G|--0--2--4--5--7--|
D|--2--3--5--7--9--|
A|--2--3--5--7--9--|
E|--0--1--3--5--7--|`}
        </pre>
      </div>
      
      <div className="relative text-center">
        <h4 
          className="mb-2"
          style={{
            fontSize: '1rem',
            fontWeight: '500',
            letterSpacing: '-0.01em',
            color: '#F5F5F5'
          }}
        >
          Tabs coming soon
        </h4>
        <p 
          style={{
            fontSize: '0.875rem',
            color: 'rgba(245, 245, 245, 0.5)',
            fontWeight: '400',
            letterSpacing: '-0.01em'
          }}
        >
          Auto-generated tablature for every instrument
        </p>
      </div>
    </div>
  );
}

function LyricsPanel() {
  return (
    <div className="min-h-[300px] flex items-center justify-center">
      <p 
        style={{
          fontSize: '0.875rem',
          color: 'rgba(245, 245, 245, 0.5)',
          fontWeight: '400',
          letterSpacing: '-0.01em'
        }}
      >
        Lyrics not available for this track.
      </p>
    </div>
  );
}