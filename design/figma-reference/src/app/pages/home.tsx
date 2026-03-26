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
