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