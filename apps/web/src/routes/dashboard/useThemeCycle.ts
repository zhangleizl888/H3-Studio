import { useCallback, useEffect, useState } from "react";
import { useApp } from "../../state/app";

/** 三态循环的存储键：auto → light → dark → auto */
const MODE_KEY = "h3studio.themeMode";

export type ThemeMode = "auto" | "light" | "dark";

export const THEME_MODE_LABEL: Record<ThemeMode, string> = {
  auto: "跟随系统",
  light: "浅色模式",
  dark: "深色模式",
};

function readStored(): ThemeMode | null {
  try {
    const v = localStorage.getItem(MODE_KEY);
    return v === "auto" || v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

/**
 * 主题三态循环，仍然只通过 useApp 的 setTheme 落到 <html data-theme>。
 *
 * 没选过就不去动用户当前的主题：把已经生效的那一档当起点，
 * 否则一进项目库就把别人在侧栏里手选的浅色覆盖掉。
 */
export function useThemeCycle() {
  const theme = useApp((s) => s.theme);
  const setTheme = useApp((s) => s.setTheme);
  const [mode, setMode] = useState<ThemeMode>(() => readStored() ?? theme);

  useEffect(() => {
    if (mode !== "auto") return;
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const apply = () => setTheme(mq.matches ? "light" : "dark");
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [mode, setTheme]);

  const cycle = useCallback(() => {
    const next: ThemeMode = mode === "auto" ? "light" : mode === "light" ? "dark" : "auto";
    setMode(next);
    try {
      localStorage.setItem(MODE_KEY, next);
    } catch {
      /* 隐私模式写不进去：这一档在本次会话里仍然生效 */
    }
    if (next !== "auto") setTheme(next);
  }, [mode, setTheme]);

  return { mode, cycle };
}
