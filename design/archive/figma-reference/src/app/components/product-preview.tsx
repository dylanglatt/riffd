export function ProductPreview() {
  return (
    <section className="py-40 px-8 border-t border-white/[0.08]" id="product">
      <div className="max-w-6xl mx-auto">
        <div 
          className="border border-white/[0.08] bg-[#0D0D0D] overflow-hidden"
          style={{
            aspectRatio: '16/10'
          }}
        >
          {/* Header */}
          <div className="border-b border-white/[0.08] px-8 py-5 flex items-center justify-between">
            <div className="flex items-center gap-6">
              <div className="flex gap-2">
                <div className="w-3 h-3 rounded-full bg-white/10" />
                <div className="w-3 h-3 rounded-full bg-white/10" />
                <div className="w-3 h-3 rounded-full bg-white/10" />
              </div>
              <div className="h-8 w-64 bg-white/[0.04] border border-white/[0.06]" />
            </div>
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 border border-white/[0.08] bg-white/[0.02]" />
              <div className="w-8 h-8 border border-white/[0.08] bg-white/[0.02]" />
            </div>
          </div>
          
          {/* Main content */}
          <div className="p-10 flex flex-col gap-8">
            {/* Waveform display */}
            <div className="border border-white/[0.06] bg-black/30 p-8">
              <div className="flex items-center justify-center gap-[1px] h-40">
                {Array.from({ length: 150 }).map((_, i) => (
                  <div
                    key={i}
                    className="w-[2px]"
                    style={{
                      height: `${Math.sin(i * 0.1) * 40 + 50}%`,
                      backgroundColor: i % 25 === 0 ? '#D4691F' : 'rgba(245, 245, 245, 0.35)',
                      opacity: i % 25 === 0 ? 0.8 : 0.5
                    }}
                  />
                ))}
              </div>
            </div>
            
            {/* Controls and info */}
            <div className="grid grid-cols-3 gap-6">
              <div className="border border-white/[0.08] bg-black/20 p-6">
                <div 
                  className="mb-3 text-white/40" 
                  style={{ 
                    fontSize: '0.8125rem',
                    fontWeight: '500',
                    letterSpacing: '0.05em'
                  }}
                >
                  KEY
                </div>
                <div 
                  className="text-white" 
                  style={{ 
                    fontSize: '1.5rem', 
                    fontWeight: '500',
                    letterSpacing: '-0.02em'
                  }}
                >
                  A Minor
                </div>
              </div>
              <div className="border border-white/[0.08] bg-black/20 p-6">
                <div 
                  className="mb-3 text-white/40" 
                  style={{ 
                    fontSize: '0.8125rem',
                    fontWeight: '500',
                    letterSpacing: '0.05em'
                  }}
                >
                  TEMPO
                </div>
                <div 
                  className="text-white" 
                  style={{ 
                    fontSize: '1.5rem', 
                    fontWeight: '500',
                    letterSpacing: '-0.02em'
                  }}
                >
                  120 BPM
                </div>
              </div>
              <div className="border border-white/[0.08] bg-black/20 p-6">
                <div 
                  className="mb-3 text-white/40" 
                  style={{ 
                    fontSize: '0.8125rem',
                    fontWeight: '500',
                    letterSpacing: '0.05em'
                  }}
                >
                  TIME
                </div>
                <div 
                  className="text-white" 
                  style={{ 
                    fontSize: '1.5rem', 
                    fontWeight: '500',
                    letterSpacing: '-0.02em'
                  }}
                >
                  4/4
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}