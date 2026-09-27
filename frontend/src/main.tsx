import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { ErrorPanel, ErrorBoundary } from "./components/common";
import Layout from "./components/Layout";
import { translate, useI18n } from "./i18n";
import { AppProvider } from "./lib/app";
import Dashboard from "./pages/Dashboard";
import Favorites from "./pages/Favorites";
import Logs from "./pages/Logs";
import Properties from "./pages/Properties";
import PropertyDetail from "./pages/PropertyDetail";
import SettingsPage from "./pages/Settings";
import Sites from "./pages/Sites";
import Statistics from "./pages/Statistics";
import TaskWizard from "./pages/TaskWizard";
import Tasks from "./pages/Tasks";
import "./styles.css";

const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <Dashboard /> },
      { path: "tasks", element: <Tasks /> },
      { path: "tasks/new", element: <TaskWizard /> },
      { path: "tasks/:id/edit", element: <TaskWizard /> },
      { path: "properties", element: <Properties /> },
      { path: "properties/:id", element: <PropertyDetail /> },
      { path: "statistics", element: <Statistics /> },
      { path: "favorites", element: <Favorites /> },
      { path: "sites", element: <Sites /> },
      { path: "logs", element: <Logs /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "*", element: <Dashboard /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppProvider
      fallback={(error, retry, lang) =>
        error ? (
          <div style={{ padding: 32 }}><ErrorPanel error={error} onRetry={retry} /></div>
        ) : (
          <div className="boot">{translate(lang, "state.loading")}</div>
        )
      }
    >
      <BootedApp />
    </AppProvider>
  </StrictMode>,
);

function BootedApp() {
  const { lang } = useI18n();
  useEffect(() => {
    document.documentElement.lang = { ja: "ja", zh: "zh-CN", en: "en" }[lang];
  }, [lang]);
  return (
    <ErrorBoundary lang={lang}>
      <RouterProvider router={router} />
    </ErrorBoundary>
  );
}
