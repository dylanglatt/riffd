export function CTA() {
  return (
    <section className="py-40 px-8 border-t border-white/[0.08]">
      <div className="max-w-3xl mx-auto text-center">
        <h2 
          className="mb-12"
          style={{
            fontSize: '3rem',
            lineHeight: '1.15',
            fontWeight: '500',
            letterSpacing: '-0.03em',
            color: '#F5F5F5'
          }}
        >
          Start analyzing music.
        </h2>
        
        <button 
          className="px-8 py-4 text-black hover:bg-[#FAFAF9] transition-all hover:-translate-y-0.5"
          style={{
            fontSize: '0.9375rem',
            fontWeight: '500',
            letterSpacing: '-0.01em',
            backgroundColor: '#FAFAF9'
          }}
        >
          Get Started
        </button>
      </div>
    </section>
  );
}