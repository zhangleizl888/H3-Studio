import { useEffect, useRef, useState } from "react";
import { useApp } from "../state/app";
import { clamp, cn } from "../lib/utils";

/**
 * 可拖拽分栏。
 *
 * 宽度按稳定 key 存进 h3studio.ui（state/app.ts 的 persist），刷新后保持。
 * 用法：分栏那一侧的元素同时挂 `pane.style`（提供 --pane-w）和 `relative`，
 * 把手作为它的子元素贴在边缘上 —— 把手是绝对定位的，不占布局，
 * 所以窄屏断点下不套 --pane-w 也不会漏出多余的一列。
 */
export function usePane(key: string, fallback: number, min: number, max: number) {
  const stored = useApp((s) => s.paneWidths[key]);
  const setPaneWidth = useApp((s) => s.setPaneWidth);
  const px = clamp(stored ?? fallback, min, max);
  return {
    key,
    px,
    min,
    max,
    fallback,
    style: { ["--pane-w" as string]: `${px}px` } as React.CSSProperties,
    commit: (v: number) => setPaneWidth(key, clamp(Math.round(v), min, max)),
    // 键盘微调必须从 store 现取当前值：连按时两次 keydown 可能落在同一次渲染里，
    // 用闭包里的 px 会把第二次的基准读成旧宽度。
    nudge: (d: number) => setPaneWidth(key, clamp(Math.round((useApp.getState().paneWidths[key] ?? fallback) + d), min, max)),
    reset: () => setPaneWidth(key, fallback),
  };
}

export type Pane = ReturnType<typeof usePane>;

/** side = 这一栏在窗口的哪一侧；左栏的把手贴在它右缘，右栏贴在左缘 */
export function SplitHandle({
  pane,
  side,
  label,
  className,
  onDragChange,
}: {
  pane: Pane;
  side: "left" | "right";
  label: string;
  className?: string;
  /** 拖动开始/结束。带 transition-[width] 的分栏要用它把过渡关掉，否则宽度会黏在指针后面 */
  onDragChange?: (dragging: boolean) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const origin = useRef({ x: 0, w: pane.px });
  const live = useRef(false);

  // 拖到一半组件被卸载（比如抽屉关掉）时，别让全局光标和禁选状态留在 body 上
  useEffect(
    () => () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    },
    [],
  );

  const begin = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    origin.current = { x: e.clientX, w: pane.px };
    live.current = true;
    setDragging(true);
    onDragChange?.(true);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  const move = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!live.current) return;
    const delta = side === "left" ? e.clientX - origin.current.x : origin.current.x - e.clientX;
    pane.commit(origin.current.w + delta);
  };

  const end = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!live.current) return;
    live.current = false;
    if (e.currentTarget.hasPointerCapture?.(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    setDragging(false);
    onDragChange?.(false);
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
  };

  const onKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 48 : 16;
    if (e.key === "ArrowRight") pane.nudge(side === "left" ? step : -step);
    else if (e.key === "ArrowLeft") pane.nudge(side === "left" ? -step : step);
    else if (e.key === "Home") pane.reset();
    else return;
    e.preventDefault();
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={`${label}（拖动调整宽度，方向键微调，双击复位）`}
      aria-valuemin={pane.min}
      aria-valuemax={pane.max}
      aria-valuenow={pane.px}
      tabIndex={0}
      title={`${label}：拖动调整宽度，双击复位到 ${pane.fallback}px`}
      onPointerDown={begin}
      onPointerMove={move}
      onPointerUp={end}
      onLostPointerCapture={end}
      onDoubleClick={pane.reset}
      onKeyDown={onKey}
      className={cn(
        "group absolute top-0 bottom-0 z-20 w-3 shrink-0 cursor-col-resize touch-none",
        side === "left" ? "right-0 translate-x-1/2" : "left-0 -translate-x-1/2",
        className,
      )}
    >
      {/* 静止时不画线：这一栏本来就有 border-l / border-r，再叠一条就是双线。
          hover / 键盘焦点 / 拖动时才点亮，告诉人这条边是可以拽的。 */}
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-colors",
          dragging ? "bg-chrome" : "bg-transparent group-hover:bg-chrome/60 group-focus-visible:bg-chrome",
        )}
      />
    </div>
  );
}
