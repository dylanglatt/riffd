import { Logo } from './logo';

export function Header() {
  return (
    <header className="fixed top-0 left-0 right-0 z-50 border-b border-white/[0.08] bg-[#0B0B0B]/80 backdrop-blur-sm">
      <div className="max-w-7xl mx-auto px-8 py-7 flex items-center justify-between">
        <Logo />
        
        <nav className="flex items-center gap-10">
          <a 
            href="#features" 
            className="text-white/50 hover:text-white transition-colors"
            style={{
              fontSize: '0.9375rem',
              fontWeight: '400',
              letterSpacing: '-0.01em'
            }}
          >
            Features
          </a>
          <a 
            href="#product" 
            className="text-white/50 hover:text-white transition-colors"
            style={{
              fontSize: '0.9375rem',
              fontWeight: '400',
              letterSpacing: '-0.01em'
            }}
          >
            Product
          </a>
          <a 
            href="#signin" 
            className="text-white/50 hover:text-white transition-colors"
            style={{
              fontSize: '0.9375rem',
              fontWeight: '400',
              letterSpacing: '-0.01em'
            }}
          >
            Sign In
          </a>
        </nav>
      </div>
    </header>
  );
}