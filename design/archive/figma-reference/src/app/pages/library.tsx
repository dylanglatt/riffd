export function LibraryPage() {
  const favorites = [
    { name: 'Peg', artist: 'Steely Dan', album: 'Aja' },
    { name: 'Dreams', artist: 'Fleetwood Mac', album: 'Rumours' },
    { name: 'Gravity', artist: 'John Mayer', album: 'Continuum' },
    { name: 'Sultans of Swing', artist: 'Dire Straits', album: 'Dire Straits' },
  ];
  
  const recommended = [
    { name: 'Kid Charlemagne', artist: 'Steely Dan', album: 'The Royal Scam' },
    { name: 'Rosanna', artist: 'Toto', album: 'Toto IV' },
    { name: 'Cliffs of Dover', artist: 'Eric Johnson', album: 'Ah Via Musicom' },
    { name: 'Sir Duke', artist: 'Stevie Wonder', album: 'Songs in the Key of Life' },
  ];
  
  return (
    <div className="min-h-screen">
      <div className="max-w-7xl mx-auto px-8 py-16">
        {/* Header */}
        <div className="mb-16">
          <div className="flex items-center gap-4 mb-4">
            <h1 
              style={{
                fontSize: '3rem',
                lineHeight: '1.1',
                fontWeight: '500',
                letterSpacing: '-0.03em',
                color: '#F5F5F5'
              }}
            >
              Library
            </h1>
            <span 
              style={{
                fontSize: '0.75rem',
                color: 'rgba(245, 245, 245, 0.35)',
                fontWeight: '400'
              }}
            >
              Coming Soon
            </span>
          </div>
          
          <p 
            className="mb-3"
            style={{
              fontSize: '1rem',
              lineHeight: '1.6',
              fontWeight: '400',
              color: 'rgba(245, 245, 245, 0.55)',
              letterSpacing: '-0.01em'
            }}
          >
            Save, organize, and revisit your analyses, stems, and practice sessions.
          </p>
          
          <p 
            style={{
              fontSize: '0.875rem',
              color: 'rgba(245, 245, 245, 0.35)',
              fontWeight: '400',
              letterSpacing: '-0.01em'
            }}
          >
            We're building this next. Here's a preview of what it will look like.
          </p>
        </div>
        
        {/* Favorites Section */}
        <section className="mb-16">
          <h2 
            className="mb-6"
            style={{
              fontSize: '0.9375rem',
              fontWeight: '500',
              letterSpacing: '-0.01em',
              color: 'rgba(245, 245, 245, 0.7)'
            }}
          >
            Favorites
          </h2>
          
          <div className="grid grid-cols-4 gap-4">
            {favorites.map((song, i) => (
              <SongCard key={i} {...song} />
            ))}
          </div>
        </section>
        
        {/* Recommended Section */}
        <section>
          <h2 
            className="mb-6"
            style={{
              fontSize: '0.9375rem',
              fontWeight: '500',
              letterSpacing: '-0.01em',
              color: 'rgba(245, 245, 245, 0.7)'
            }}
          >
            Recommended for You
          </h2>
          
          <div className="grid grid-cols-4 gap-4">
            {recommended.map((song, i) => (
              <SongCard key={i} {...song} />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

interface SongCardProps {
  name: string;
  artist: string;
  album: string;
}

function SongCard({ name, artist, album }: SongCardProps) {
  return (
    <div className="opacity-60 hover:opacity-75 transition-opacity">
      <div 
        className="w-full aspect-square border border-white/[0.08] bg-[#0D0D0D] mb-3"
      />
      
      <h4 
        className="mb-1 truncate"
        style={{
          fontSize: '0.875rem',
          fontWeight: '500',
          letterSpacing: '-0.01em',
          color: '#F5F5F5'
        }}
      >
        {name}
      </h4>
      
      <p 
        className="mb-1 truncate"
        style={{
          fontSize: '0.8125rem',
          color: 'rgba(245, 245, 245, 0.5)',
          fontWeight: '400',
          letterSpacing: '-0.01em'
        }}
      >
        {artist}
      </p>
      
      <p 
        className="truncate"
        style={{
          fontSize: '0.75rem',
          color: 'rgba(245, 245, 245, 0.35)',
          fontWeight: '400',
          letterSpacing: '-0.01em'
        }}
      >
        {album}
      </p>
    </div>
  );
}
