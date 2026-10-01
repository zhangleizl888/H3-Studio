/**
 * 行级差异：只服务一件事 —— 让创作者在「整篇替换编辑器正文」之前看清动了哪些行。
 *
 * 不做字符级、不做 side-by-side：剧本是按行组织的（一行一个动作/一句台词），
 * 行的增删改就是创作者关心的粒度，再细只会让预览变成噪声。
 */

export type DiffLine = { t: "same" | "add" | "del"; text: string };

/** LCS 是 O(n×m)：中段超过这个行数就不算了，直接整段标成删除+新增 */
const MAX_LCS_LINES = 700;

export interface DiffResult {
  lines: DiffLine[];
  added: number;
  removed: number;
  /** 中段太大、退化成「整段替换」的那种显示 */
  coarse: boolean;
}

export function diffLines(before: string, after: string): DiffResult {
  const a = before.split("\n");
  const b = after.split("\n");
  // 先掐掉公共首尾：改一场戏，前后几百行没必要进 LCS 表
  let lo = 0;
  while (lo < a.length && lo < b.length && a[lo] === b[lo]) lo++;
  let ha = a.length;
  let hb = b.length;
  while (ha > lo && hb > lo && a[ha - 1] === b[hb - 1]) {
    ha--;
    hb--;
  }
  const mid = a.slice(lo, ha);
  const tgt = b.slice(lo, hb);
  const coarse = mid.length > MAX_LCS_LINES || tgt.length > MAX_LCS_LINES;
  const body = coarse ? coarseDiff(mid, tgt) : lcsDiff(mid, tgt);
  const lines: DiffLine[] = [
    ...a.slice(0, lo).map((text) => ({ t: "same" as const, text })),
    ...body,
    ...a.slice(ha).map((text) => ({ t: "same" as const, text })),
  ];
  return {
    lines,
    added: lines.filter((l) => l.t === "add").length,
    removed: lines.filter((l) => l.t === "del").length,
    coarse,
  };
}

function coarseDiff(a: string[], b: string[]): DiffLine[] {
  return [...a.map((text) => ({ t: "del" as const, text })), ...b.map((text) => ({ t: "add" as const, text }))];
}

function lcsDiff(a: string[], b: string[]): DiffLine[] {
  const n = a.length;
  const m = b.length;
  const w = m + 1;
  const dp = new Uint32Array((n + 1) * w);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i * w + j] = a[i] === b[j] ? dp[(i + 1) * w + j + 1] + 1 : Math.max(dp[(i + 1) * w + j], dp[i * w + j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ t: "same", text: a[i] });
      i++;
      j++;
    } else if (dp[(i + 1) * w + j] >= dp[i * w + j + 1]) {
      out.push({ t: "del", text: a[i++] });
    } else {
      out.push({ t: "add", text: b[j++] });
    }
  }
  while (i < n) out.push({ t: "del", text: a[i++] });
  while (j < m) out.push({ t: "add", text: b[j++] });
  return out;
}

/** 差异太长会把悬浮面板撑爆：只放前 maxChanged 处变更，剩下的如实说还有多少 */
export function takeHunks(diff: DiffResult, maxChanged = 120): { shown: DiffLine[]; hidden: number } {
  let seen = 0;
  const shown: DiffLine[] = [];
  for (const l of diff.lines) {
    if (l.t !== "same") {
      if (seen >= maxChanged) break;
      seen++;
    }
    shown.push(l);
  }
  return { shown, hidden: diff.added + diff.removed - seen };
}
