# Complete Riffd Implementation Guide

## Overview
This is a complete, exhaustive guide to implement the Riffd multi-page music-tech application with a premium, minimalist design system inspired by Apple, Linear, Stripe, and Arc.

---

## Design System Foundation

### Color Palette
```
Primary Background: #0B0B0B (deep charcoal-black)
Secondary Background: #0D0D0D (slightly lighter for cards)
Text Primary: #F5F5F5 (off-white)
Text Secondary: rgba(245, 245, 245, 0.55) (warm gray)
Text Muted: rgba(245, 245, 245, 0.35) (very muted gray)
Accent Orange: #D4691F (Gibson Les Paul sunburst - used sparingly)
Border Color: rgba(255, 255, 255, 0.08) (very subtle white borders)
Button Background: #FAFAF9 (off-white cream)
```

### Typography
```
Font Family: Inter (or system sans-serif)
Large Headlines: 4rem (64px), font-weight: 500, line-height: 1.05, letter-spacing: -0.04em
Medium Headlines: 3rem (48px), font-weight: 500, line-height: 1.1, letter-spacing: -0.03em
Section Headlines: 2rem (32px), font-weight: 500
Body Large: 1.125rem (18px), line-height: 1.65, letter-spacing: -0.01em
Body Default: 0.9375rem (15px), letter-spacing: -0.01em
Small Text: 0.875rem (14px)
Micro Text: 0.75rem (12px)
```

### Design Principles
1. **Extreme Spacing** - 120px+ between sections, generous padding
2. **Thin Borders Only** - Use 1px borders at 8-20% opacity, no fills
3. **No Gradients/Glows** - Flat, clean, editorial style
4. **Orange for Icons Only** - Accent color only on small icons and occasional highlights
5. **Typography-Driven** - Let large type and spacing do the work
6. **Monochromatic** - Black background, white text, warm gray secondary text

---

## Project Structure

```
/src
  /app
    /components
      header.tsx
      hero.tsx
      features.tsx
      product-preview.tsx
      cta.tsx
      footer.tsx
      navigation.tsx
      logo.tsx
    /layouts
      root-layout.tsx
    /pages
      home.tsx
      decompose.tsx
      studio.tsx
      library.tsx
      practice.tsx
      about.tsx
    App.tsx
    routes.tsx
  /styles
    index.css
    theme.css
    tailwind.css
    fonts.css
package.json
```

---

## Package Dependencies

```json
{
  "dependencies": {
    "lucide-react": "^0.487.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router": "^7.13.0"
  },
  "devDependencies": {
    "@tailwindcss/vite": "^4.1.12",
    "@vitejs/plugin-react": "^4.7.0",
    "tailwindcss": "^4.1.12",
    "vite": "^6.3.5"
  }
}
```

---

## Complete File Contents

### `/src/app/App.tsx`
```tsx
import { RouterProvider } from 'react-router';
import { router } from './routes';

export default function App() {
  return <RouterProvider router={router} />;
}
```

### `/src/app/routes.tsx`
```tsx
import { createBrowserRouter } from "react-router";
import { RootLayout } from "./layouts/root-layout";
import { HomePage } from "./pages/home";
import { DecomposePage } from "./pages/decompose";
import { StudioPage } from "./pages/studio";
import { LibraryPage } from "./pages/library";
import { PracticePage } from "./pages/practice";
import { AboutPage } from "./pages/about";

export const router = createBrowserRouter([
  {
    path: "/",
    Component: RootLayout,
    children: [
      { index: true, Component: HomePage },
      { path: "decompose", Component: DecomposePage },
      { path: "studio", Component: StudioPage },
      { path: "library", Component: LibraryPage },
      { path: "practice", Component: PracticePage },
      { path: "about", Component: AboutPage },
    ],
  },
]);
```

### `/src/app/layouts/root-layout.tsx`
```tsx
import { Outlet } from 'react-router';
import { Navigation } from '../components/navigation';

export function RootLayout() {
  return (
    <div className="min-h-screen bg-[#0B0B0B]">
      <Navigation />
      <main>
        <Outlet />
      </main>
    </div>
  );
}
```

### `/src/app/components/navigation.tsx`
```tsx
import { Link, useLocation } from 'react-router';

export function Navigation() {
  const location = useLocation();
  
  const isActive = (path: string) => {
    if (path === '/') return location.pathname === '/';
    return location.pathname.startsWith(path);
  };
  
  return (
    <nav className="border-b border-white/[0.08] bg-[#0B0B0B]">
      <div className="max-w-7xl mx-auto px-8 py-6 flex items-center justify-between">
        {/* Logo */}
        <Link 
          to="/"
          style={{
            fontSize: '1.125rem',
            fontWeight: '500',
            color: '#F5F5F5',
            letterSpacing: '-0.02em'
          }}
        >
          riffd
        </Link>
        
        {/* Nav Links */}
        <div className="flex items-center gap-8">
          <NavLink to="/decompose" isActive={isActive('/decompose')}>
            Decompose
          </NavLink>
          
          <NavLink to="/studio" isActive={isActive('/studio')} badge="Beta">
            Studio
          </NavLink>
          
          <NavLink to="/library" isActive={isActive('/library')}>
            Library
          </NavLink>
          
          <NavLink to="/practice" isActive={isActive('/practice')} badge="Coming Soon">
            Practice
          </NavLink>
        </div>
      </div>
    </nav>
  );
}

interface NavLinkProps {
  to: string;
  isActive: boolean;
  badge?: string;
  children: React.ReactNode;
}

function NavLink({ to, isActive, badge, children }: NavLinkProps) {
  return (
    <Link
      to={to}
      className="flex items-center gap-2 transition-colors"
      style={{
        fontSize: '0.9375rem',
        fontWeight: '400',
        color: isActive ? '#F5F5F5' : 'rgba(245, 245, 245, 0.5)',
        letterSpacing: '-0.01em'
      }}
    >
      {children}
      {badge && (
        <span
          style={{
            fontSize: '0.6875rem',
            color: 'rgba(245, 245, 245, 0.35)',
            fontWeight: '400'
          }}
        >
          {badge}
        </span>
      )}
    </Link>
  );
}
```

### `/src/app/components/logo.tsx`
```tsx
export function Logo() {
  return (
    <div 
      className="text-white"
      style={{
        fontSize: '1.25rem',
        fontWeight: '400',
        letterSpacing: '-0.04em'
      }}
    >
      riffd
    </div>
  );
}
```

### `/src/app/components/hero.tsx`
```tsx
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
```

### `/src/app/components/features.tsx`
```tsx
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
```

### `/src/app/components/product-preview.tsx`
```tsx
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
```

### `/src/app/components/cta.tsx`
```tsx
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
```

### `/src/app/components/footer.tsx`
```tsx
export function Footer() {
  return (
    <footer className="py-16 px-8 border-t border-white/[0.08]">
      <div className="max-w-7xl mx-auto">
        <div className="flex flex-col md:flex-row items-center justify-between gap-6">
          <p 
            style={{
              fontSize: '0.875rem',
              fontWeight: '400',
              color: 'rgba(245, 245, 245, 0.35)',
              letterSpacing: '-0.01em'
            }}
          >
            © 2026 riffd. All rights reserved.
          </p>
          
          <div className="flex items-center gap-10">
            <a 
              href="#privacy" 
              className="hover:text-white transition-colors"
              style={{
                fontSize: '0.875rem',
                fontWeight: '400',
                color: 'rgba(245, 245, 245, 0.35)',
                letterSpacing: '-0.01em'
              }}
            >
              Privacy
            </a>
            <a 
              href="#terms" 
              className="hover:text-white transition-colors"
              style={{
                fontSize: '0.875rem',
                fontWeight: '400',
                color: 'rgba(245, 245, 245, 0.35)',
                letterSpacing: '-0.01em'
              }}
            >
              Terms
            </a>
            <a 
              href="#contact" 
              className="hover:text-white transition-colors"
              style={{
                fontSize: '0.875rem',
                fontWeight: '400',
                color: 'rgba(245, 245, 245, 0.35)',
                letterSpacing: '-0.01em'
              }}
            >
              Contact
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}
```

### `/src/app/pages/home.tsx`
```tsx
import { Hero } from '../components/hero';
import { Features } from '../components/features';
import { ProductPreview } from '../components/product-preview';
import { CTA } from '../components/cta';
import { Footer } from '../components/footer';

export function HomePage() {
  return (
    <>
      <Hero />
      <Features />
      <ProductPreview />
      <CTA />
      <Footer />
    </>
  );
}
```

### `/src/app/pages/decompose.tsx`
```tsx
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
```

### `/src/app/pages/studio.tsx`
```tsx
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
```

### `/src/app/pages/library.tsx`
```tsx
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
```

### `/src/app/pages/practice.tsx`
```tsx
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
```

### `/src/app/pages/about.tsx`
```tsx
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
```

---

## CSS Setup

### `/src/styles/index.css`
```css
@import 'tailwindcss';
@import './fonts.css';
@import './theme.css';

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  padding: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', 'Roboto', 'Oxygen',
    'Ubuntu', 'Cantarell', 'Fira Sans', 'Droid Sans', 'Helvetica Neue',
    sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  background: #0B0B0B;
  color: #F5F5F5;
}
```

---

## Implementation Instructions for Claude

### Step 1: Setup
```bash
# Initialize React + Vite + TypeScript project
npm create vite@latest riffd -- --template react-ts

# Install dependencies
npm install lucide-react react-router

# Install Tailwind CSS v4
npm install -D tailwindcss@next @tailwindcss/vite@next
```

### Step 2: File Structure
Create all files exactly as shown above in the proper directory structure.

### Step 3: Key Implementation Notes

1. **Use inline styles for typography** - The design system requires exact font sizes, weights, and spacing that aren't in Tailwind by default
2. **Border opacity syntax** - Use `border-white/[0.08]` for 8% white opacity borders
3. **Background colors** - Main: `#0B0B0B`, Cards: `#0D0D0D`
4. **Orange accent** - `#D4691F` - ONLY use on icons and small highlights
5. **Waveform visualizations** - Use `Array.from({ length: N }).map((_, i) => ...)` with random heights
6. **Router** - Must use `react-router` (NOT `react-router-dom`) v7+
7. **Navigation** - Global nav in RootLayout, shows on every page except home

### Step 4: Design Checklist
- ✅ Pure dark background (#0B0B0B)
- ✅ Large regular-weight headlines (4rem, weight 500)
- ✅ Thin borders only (1px at 8-15% opacity)
- ✅ Orange ONLY on icons/small accents
- ✅ Off-white buttons (#FAFAF9)
- ✅ No gradients, no glows, no glassmorphism
- ✅ Generous spacing (40-48px padding, 120px+ between sections)
- ✅ Typography-driven layouts
- ✅ Monochromatic palette

### Step 5: Page-Specific Notes

**Decompose Page:**
- 3 views with state management
- Auto-advance from Processing to Results after 3 seconds
- Tab system with active underline in orange
- Volume sliders with orange thumbs

**Studio Page:**
- Sidebar with orange active indicator (left border)
- Theory cards in 3-column grid
- Monospace orange formulas

**Library Page:**
- "Coming Soon" badge
- Reduced opacity (60%) on all cards
- 4-column grid

**Practice Page:**
- 2-column grid of module cards
- Orange bullet points
- Monospace preview boxes

**About Page:**
- Centered layout
- Pipeline steps with numbered circles
- 3-column tech card grid
- Uppercase section headers

---

## Critical Reminders

1. **NO gradients** - Flat colors only
2. **NO glows/shadows** - Borders do structural work
3. **Orange sparingly** - Icons and occasional highlights ONLY
4. **Large type** - Don't be afraid of 64px headlines
5. **Spacing** - Err on the side of MORE space, not less
6. **Borders** - 1px, 8-15% white opacity
7. **Button style** - Off-white fill with dark text OR ghost with border
8. **Waveforms** - Thin vertical bars, occasional orange bars for accent

---

## Testing Checklist

- [ ] All routes work (/, /decompose, /studio, /library, /practice, /about)
- [ ] Navigation highlights active page
- [ ] Decompose view transitions work
- [ ] Tab system in Decompose Results works
- [ ] Studio sidebar navigation works
- [ ] All typography matches design system
- [ ] Orange only appears on icons/small highlights
- [ ] Borders are thin and subtle
- [ ] Spacing feels generous and breathable
- [ ] No console errors
- [ ] Responsive on desktop (1440px+)

---

This document contains EVERYTHING needed to recreate the Riffd application exactly as designed.
