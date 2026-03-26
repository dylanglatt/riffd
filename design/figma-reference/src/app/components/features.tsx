import { Layers, Music, Play } from 'lucide-react';

const features = [
  {
    icon: Layers,
    title: 'Isolate',
    description: 'Separate vocals, drums, bass, and instruments with precision.'
  },
  {
    icon: Music,
    title: 'Understand',
    description: 'See chords, structure, and key in real time.'
  },
  {
    icon: Play,
    title: 'Play',
    description: 'Generate tabs and practice any part of the song.'
  }
];

export function Features() {
  return (
    <section className="py-40 px-8 border-t border-white/[0.08]" id="features">
      <div className="max-w-7xl mx-auto">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-16">
          {features.map((feature, index) => (
            <div key={index}>
              <feature.icon 
                className="w-6 h-6 mb-6"
                strokeWidth={1.5}
                style={{ color: '#D4691F', opacity: 0.85 }}
              />
              
              <h3 
                className="mb-4"
                style={{
                  fontSize: '1.25rem',
                  fontWeight: '500',
                  letterSpacing: '-0.02em',
                  color: '#F5F5F5'
                }}
              >
                {feature.title}
              </h3>
              
              <p 
                style={{
                  fontSize: '0.9375rem',
                  lineHeight: '1.65',
                  fontWeight: '400',
                  color: 'rgba(245, 245, 245, 0.5)',
                  letterSpacing: '-0.01em'
                }}
              >
                {feature.description}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}