import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { MotionProvider } from "./motion/primitives";
import { queryClient } from "./lib/api";
import { Toasts, Skeletons } from "./components/ui";
import { Layout } from "./app/Layout";
const Conflicts = lazy(() =>
  import("./features/advanced/Advanced").then((m) => ({ default: m.Conflicts })),
);
const Health = lazy(() =>
  import("./features/advanced/Advanced").then((m) => ({ default: m.Health })),
);
const Demand = lazy(() =>
  import("./features/advanced/Advanced").then((m) => ({ default: m.Demand })),
);
const Links = lazy(() =>
  import("./features/advanced/Advanced").then((m) => ({ default: m.Links })),
);
const Agent = lazy(() =>
  import("./features/advanced/Advanced").then((m) => ({ default: m.Agent })),
);
const MockPanel = lazy(() => import("./dev/MockControlPanel"));
const Lab = lazy(() => import("./features/lab/Lab"));
const Experiments = lazy(() => import("./features/experiments/Experiments"));
const ML = lazy(() => import("./features/ml/ML"));
const Devices = lazy(() => import("./features/devices/Devices"));
const Home = lazy(() => import("./features/home/Home"));
const Tree = lazy(() => import("./features/tree/Tree"));
const Intent = lazy(() => import("./features/intent/Intent"));
const Decisions = lazy(() => import("./features/decisions/Decisions"));
const Settings = lazy(() => import("./features/settings/Settings"));
const Metrics = lazy(() => import("./features/metrics/Metrics"));
const Public = lazy(() =>
  import("./features/public/PublicPages").then((m) => ({ default: m.Landing })),
);
const Install = lazy(() =>
  import("./features/public/PublicPages").then((m) => ({ default: m.Install })),
);
const Connect = lazy(() =>
  import("./features/public/PublicPages").then((m) => ({ default: m.Connect })),
);
const About = lazy(() =>
  import("./features/public/PublicPages").then((m) => ({ default: m.About })),
);
export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <MotionProvider>
        <BrowserRouter>
          <Suspense
            fallback={
              <div className="route-loading">
                <Skeletons />
              </div>
            }
          >
            <Routes>
              <Route path="/welcome" element={<Public />} />
              <Route path="/install" element={<Install />} />
              <Route path="/connect" element={<Connect />} />
              <Route path="/about" element={<About />} />
              <Route path="/" element={<Navigate to="/app" replace />} />
              <Route path="/app" element={<Layout />}>
                <Route index element={<Home />} />
                <Route path="tree" element={<Tree />} />
                <Route path="metrics" element={<Metrics />} />
                <Route path="conflicts" element={<Conflicts />} />
                <Route path="health" element={<Health />} />
                <Route path="demand" element={<Demand />} />
                <Route path="links" element={<Links />} />
                <Route path="agent" element={<Agent />} />
                <Route path="lab" element={<Lab />} />
                <Route path="experiments" element={<Experiments />} />
                <Route path="ml" element={<ML />} />
                <Route path="devices" element={<Devices />} />
                <Route path="intent" element={<Intent />} />
                <Route path="decisions" element={<Decisions />} />
                <Route path="settings" element={<Settings />} />
                <Route path="about" element={<About embedded />} />
                <Route path="*" element={<Navigate to="/app" replace />} />
              </Route>
              <Route path="*" element={<Navigate to="/app" replace />} />
            </Routes>
          </Suspense>
          <Toasts />
          {import.meta.env.DEV && (
            <Suspense fallback={null}>
              <MockPanel />
            </Suspense>
          )}
        </BrowserRouter>
      </MotionProvider>
    </QueryClientProvider>
  );
}
