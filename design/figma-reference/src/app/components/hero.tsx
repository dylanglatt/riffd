export function Hero() {
  return (
    <section className="pt-48 pb-40 px-8">
      <div className="max-w-7xl mx-auto">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-20 items-center">
          {/* Left side - Content */}
          <div>
            <h1 
              className="mb-9"
              style={{
                fontSize: '4rem',
                lineHeight: '1.05',
                fontWeight: '500',
                letterSpacing: '-0.04em',
                color: '#F5F5F5'
              }}
            >
              Analyze any song.
            </h1>
            
            <p 
              className="mb-12 max-w-lg"
              style={{
                fontSize: '1.125rem',
                lineHeight: '1.65',
                fontWeight: '400',
                color: 'rgba(245, 245, 245, 0.55)',
                letterSpacing: '-0.01em'
              }}
            >
              Isolate stems, identify chords, and generate tabs — so you understand how songs actually work.
            </p>
            
            <div className="flex items-center gap-4">
              <button 
                className="px-7 py-3.5 text-black hover:bg-[#FAFAF9] transition-all hover:-translate-y-0.5"
                style={{
                  fontSize: '0.9375rem',
                  fontWeight: '500',
                  letterSpacing: '-0.01em',
                  backgroundColor: '#FAFAF9'
                }}
              >
                Get Started
              </button>
              <button 
                className="px-7 py-3.5 border text-white hover:border-white/30 transition-all hover:-translate-y-0.5"
                style={{
                  fontSize: '0.9375rem',
                  fontWeight: '500',
                  letterSpacing: '-0.01em',
                  borderColor: 'rgba(255, 255, 255, 0.15)'
                }}
              >
                See Demo
              </button>
            </div>
          </div>
          
          {/* Right side - Product Mockup */}
          <div>
            <div 
              className="border border-white/[0.08] bg-[#0D0D0D] overflow-hidden"
              style={{
                aspectRatio: '4/3'
              }}
            >
              {/* Mockup header */}
              <div className="border-b border-white/[0.08] px-6 py-4 flex items-center gap-3">
                <div className="flex gap-2">
                  <div className="w-3 h-3 rounded-full bg-white/10" />
                  <div className="w-3 h-3 rounded-full bg-white/10" />
                  <div className="w-3 h-3 rounded-full bg-white/10" />
                </div>
              </div>
              
              {/* Waveform area */}
              <div className="p-8">
                <div className="bg-black/40 border border-white/[0.06] p-6">
                  <div className="flex items-center justify-center gap-[1px] h-32">
                    {Array.from({ length: 100 }).map((_, i) => (
                      <div
                        key={i}
                        className="w-[2px]"
                        style={{
                          height: `${Math.random() * 100}%`,
                          backgroundColor: i % 20 === 0 ? '#D4691F' : 'rgba(245, 245, 245, 0.3)',
                          opacity: i % 20 === 0 ? 0.7 : undefined
                        }}
                      />
                    ))}
                  </div>
                </div>
                
                {/* Chord labels */}
                <div className="mt-6 flex items-center gap-4">
                  <div className="px-4 py-2 border border-white/[0.08] bg-black/20">
                    <span 
                      className="text-white/90" 
                      style={{ 
                        fontSize: '0.875rem',
                        fontWeight: '500',
                        letterSpacing: '-0.01em'
                      }}
                    >
                      Am
                    </span>
                  </div>
                  <div className="px-4 py-2 border border-white/[0.08] bg-black/20">
                    <span 
                      className="text-white/90" 
                      style={{ 
                        fontSize: '0.875rem',
                        fontWeight: '500',
                        letterSpacing: '-0.01em'
                      }}
                    >
                      F
                    </span>
                  </div>
                  <div className="px-4 py-2 border border-white/[0.08] bg-black/20">
                    <span 
                      className="text-white/90" 
                      style={{ 
                        fontSize: '0.875rem',
                        fontWeight: '500',
                        letterSpacing: '-0.01em'
                      }}
                    >
                      C
                    </span>
                  </div>
                  <div className="px-4 py-2 border border-white/[0.08] bg-black/20">
                    <span 
                      className="text-white/90" 
                      style={{ 
                        fontSize: '0.875rem',
                        fontWeight: '500',
                        letterSpacing: '-0.01em'
                      }}
                    >
                      G
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}