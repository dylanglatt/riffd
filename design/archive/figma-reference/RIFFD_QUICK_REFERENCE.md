# Riffd Implementation - Quick Reference

**Full implementation guide is in `/COMPLETE_IMPLEMENTATION_GUIDE.md` in this project.**

## Project Link
Share this project folder or the `/COMPLETE_IMPLEMENTATION_GUIDE.md` file.

## Design System At A Glance

### Colors
- **Background**: `#0B0B0B` (main), `#0D0D0D` (cards)
- **Text**: `#F5F5F5` (primary), `rgba(245, 245, 245, 0.55)` (secondary)
- **Accent**: `#D4691F` (orange - icons only)
- **Borders**: `rgba(255, 255, 255, 0.08)` (subtle white)
- **Buttons**: `#FAFAF9` (cream white)

### Typography
- **Large Headlines**: 4rem (64px), weight 500, letter-spacing -0.04em
- **Medium Headlines**: 3rem (48px), weight 500, letter-spacing -0.03em
- **Body**: 0.9375rem (15px), letter-spacing -0.01em
- **Font**: Inter or system sans-serif

### Design Rules
1. Extreme spacing (120px+ between sections)
2. Thin borders only (1px at 8-20% opacity)
3. NO gradients, NO glows
4. Orange ONLY on icons/small highlights
5. Typography-driven layouts
6. Monochromatic palette

## Pages Implemented
1. **Home** - Landing page with hero, features, product preview
2. **Decompose** - 3-view workflow (Search → Processing → Results)
3. **Studio** - Music theory reference with sidebar navigation
4. **Library** - Saved songs grid (Coming Soon preview)
5. **Practice** - Training modules (Coming Soon preview)
6. **About** - Technology stack and principles

## Tech Stack
- React + TypeScript
- React Router v7
- Tailwind CSS v4
- Lucide React icons

## File Structure
```
/src/app
  /components
    navigation.tsx
    hero.tsx
    features.tsx
    product-preview.tsx
    cta.tsx
    footer.tsx
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
```

## Key Implementation Details

### Navigation Active State
Orange left border (1px, #D4691F) for active sidebar links  
Full-opacity text for active nav items  
50% opacity for inactive

### Waveform Visualizations
```tsx
{Array.from({ length: 100 }).map((_, i) => (
  <div
    key={i}
    className="w-[2px]"
    style={{
      height: `${Math.random() * 100}%`,
      backgroundColor: i % 20 === 0 ? '#D4691F' : 'rgba(245, 245, 245, 0.3)'
    }}
  />
))}
```

### Border Syntax
- Tailwind: `border-white/[0.08]` for 8% opacity
- Inline: `borderColor: 'rgba(255, 255, 255, 0.08)'`

### Button Styles
**Primary (filled)**:
```tsx
style={{
  backgroundColor: '#FAFAF9',
  fontSize: '0.9375rem',
  fontWeight: '500',
  letterSpacing: '-0.01em'
}}
className="px-7 py-3.5 text-black hover:bg-[#FAFAF9] transition-all hover:-translate-y-0.5"
```

**Secondary (ghost)**:
```tsx
style={{
  fontSize: '0.9375rem',
  fontWeight: '500',
  letterSpacing: '-0.01em',
  borderColor: 'rgba(255, 255, 255, 0.15)'
}}
className="px-7 py-3.5 border text-white hover:border-white/30 transition-all hover:-translate-y-0.5"
```

## Routing Pattern
```tsx
// routes.tsx
import { createBrowserRouter } from "react-router";

export const router = createBrowserRouter([
  {
    path: "/",
    Component: RootLayout,
    children: [
      { index: true, Component: HomePage },
      { path: "decompose", Component: DecomposePage },
      // ... etc
    ],
  },
]);

// App.tsx
import { RouterProvider } from 'react-router';
import { router } from './routes';

export default function App() {
  return <RouterProvider router={router} />;
}
```

## Design System Checklist
- [ ] Pure dark background (#0B0B0B)
- [ ] Large regular-weight headlines (64px, weight 500)
- [ ] Thin borders only (1px at 8-15% opacity)
- [ ] Orange ONLY on icons/small accents
- [ ] Off-white buttons (#FAFAF9)
- [ ] No gradients, no glows, no glassmorphism
- [ ] Generous spacing (40-48px padding, 120px+ between sections)
- [ ] Typography-driven layouts
- [ ] Monochromatic palette

---

**For complete code of every single component and page, see `/COMPLETE_IMPLEMENTATION_GUIDE.md`**
