import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { setApi } from "./lib/apiClient";
import { useApp } from "./state/app";
import "./index.css";

const qc = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 8_000, retry: 1, refetchOnWindowFocus: false },
    mutations: { retry: 0 },
  },
});

// 不用顶层 await：在 vite 默认的 build.target 下会让生产构建失败
void (async () => {
  const { getApi } = await import("./lib/api");
  setApi(await getApi());

  // 主题在首帧前落到 <html>，避免闪烁
  document.documentElement.dataset.theme = useApp.getState().theme;

  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <QueryClientProvider client={qc}>
        <App />
      </QueryClientProvider>
    </StrictMode>,
  );
})();
