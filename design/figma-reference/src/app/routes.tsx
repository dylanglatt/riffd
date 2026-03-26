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
