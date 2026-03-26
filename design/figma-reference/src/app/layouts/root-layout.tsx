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
