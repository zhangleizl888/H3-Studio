import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppShell, PcWall } from "./components/layout/AppShell";
import Login from "./routes/Login";
import Dashboard from "./routes/Dashboard";
import Script from "./routes/project/Script";
import Assets from "./routes/project/Assets";
import Director from "./routes/project/Director";
import ProjectQueue from "./routes/project/Queue";
import Export from "./routes/project/Export";
import Prompts from "./routes/project/Prompts";
import Skills from "./routes/Skills";
import History from "./routes/History";
import Trash from "./routes/Trash";
import ServersWorkflows from "./routes/settings/ServersWorkflows";
import Llm from "./routes/settings/Llm";
import Users from "./routes/settings/Users";
import System from "./routes/settings/System";
import { useApi } from "./lib/apiClient";
import { useApp } from "./state/app";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const api = useApi();
  const { user, setUser } = useApp();
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let alive = true;
    api.auth
      .me()
      .then((u) => alive && setUser(u))
      .finally(() => alive && setChecking(false));
    return () => {
      alive = false;
    };
  }, [api, setUser]);

  if (checking) return null;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);
  return null;
}

export default function App() {
  return (
    <BrowserRouter>
      <PcWall>
        <ScrollToTop />
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            element={
              <RequireAuth>
                <AppShell />
              </RequireAuth>
            }
          >
            <Route path="/" element={<Dashboard />} />
            <Route path="/p/:id/script" element={<Script />} />
            <Route path="/p/:id/assets" element={<Assets />} />
            <Route path="/p/:id/director" element={<Director />} />
            <Route path="/p/:id/queue" element={<ProjectQueue />} />
            <Route path="/p/:id/prompts" element={<Prompts />} />
            <Route path="/p/:id/export" element={<Export />} />
            {/* 工作流库已经并进「设置 · 工作流管理」：这里只留一条跳转，别让老书签 404 */}
            <Route path="/workflows" element={<Navigate to="/settings/gen?tab=workflows" replace />} />
            <Route path="/skills" element={<Skills />} />
            <Route path="/history" element={<History />} />
            <Route path="/trash" element={<Trash />} />
            <Route path="/settings" element={<Navigate to="/settings/gen" replace />} />
            <Route path="/settings/gen" element={<ServersWorkflows />} />
            <Route path="/settings/llm" element={<Llm />} />
            <Route path="/settings/users" element={<Users />} />
            <Route path="/settings/system" element={<System />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </PcWall>
    </BrowserRouter>
  );
}

function NotFound() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-ink-mute">
      <div className="text-body text-ink-dim">这个地址没有对应的页面</div>
      <div className="mono text-caption">检查地址里的项目 id 是否还在</div>
    </div>
  );
}
