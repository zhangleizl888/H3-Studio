import type { Api } from "./api";

let inst: Api | null = null;

/** main.tsx 在渲染前解析好实现（mock 或 http），页面通过 useApi 拿 */
export function setApi(a: Api) {
  inst = a;
}

export function useApi(): Api {
  if (!inst) throw new Error("Api 尚未初始化");
  return inst;
}
