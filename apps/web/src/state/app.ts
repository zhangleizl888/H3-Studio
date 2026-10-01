import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { User } from "../lib/types";

type Theme = "dark" | "light";

interface AppState {
  theme: Theme;
  user: User | null;
  /** 侧栏折叠状态：导演台需要横向空间，允许整屏收起 */
  navCollapsed: boolean;
  /** 各处分栏的宽度，按稳定的 key 存（director.drawer / script.config / …） */
  paneWidths: Record<string, number>;
  setTheme(t: Theme): void;
  toggleTheme(): void;
  setUser(u: User | null): void;
  setNavCollapsed(v: boolean): void;
  setPaneWidth(key: string, px: number): void;
}

export const useApp = create<AppState>()(
  persist(
    (set, get) => ({
      theme: "dark",
      user: null,
      navCollapsed: false,
      paneWidths: {},
      setTheme: (theme) => {
        set({ theme });
        document.documentElement.dataset.theme = theme;
      },
      toggleTheme: () => get().setTheme(get().theme === "dark" ? "light" : "dark"),
      setUser: (user) => set({ user }),
      setNavCollapsed: (navCollapsed) => set({ navCollapsed }),
      setPaneWidth: (key, px) => set((s) => ({ paneWidths: { ...s.paneWidths, [key]: px } })),
    }),
    {
      name: "h3studio.ui",
      partialize: (s) => ({ theme: s.theme, navCollapsed: s.navCollapsed, paneWidths: s.paneWidths }),
    },
  ),
);

