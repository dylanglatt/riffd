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
