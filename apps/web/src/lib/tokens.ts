/**
 * 令牌存放。
 *
 * localStorage 挡不住 XSS，是明知故犯：这是内网桌面工具，服务只绑环回/局域网，
 * 而 <img src> 这类子资源带不上 Authorization 头，用 httpOnly Cookie 反而要把
 * 后端的 CORS 与 SameSite 一起改。真要暴露到公网，这里必须换成 Cookie + CSRF。
 */

const KEY = "h3studio.tokens";

interface Stored {
  access: string;
  refresh: string;
  /** 拿到令牌的时刻与有效期，用来在本地提前判断「这个 access 多半已经过期」 */
  gotAt: number;
  expiresIn: number;
}

let mem: Stored | null = null;

function read(): Stored | null {
  if (mem) return mem;
  try {
    const raw = localStorage.getItem(KEY);
    mem = raw ? (JSON.parse(raw) as Stored) : null;
  } catch {
    mem = null;
  }
  return mem;
}

export function setTokens(t: { access: string; refresh: string; expires_in?: number }): void {
  mem = { access: t.access, refresh: t.refresh, gotAt: Date.now(), expiresIn: t.expires_in ?? 900 };
  try {
    localStorage.setItem(KEY, JSON.stringify(mem));
  } catch {
    // 隐私模式下写不进去：内存里那份还能用到本次会话结束
  }
}

export function clearTokens(): void {
  mem = null;
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* 忽略 */
  }
}

export function accessToken(): string | null {
  const s = read();
  if (!s) return null;
  // 提前 15 秒算过期，免得把注定 401 的请求打出去
  if (Date.now() > s.gotAt + (s.expiresIn - 15) * 1000) return null;
  return s.access;
}

export function refreshTokenValue(): string | null {
  return read()?.refresh ?? null;
}

export function isSignedIn(): boolean {
  return !!read();
}

/**
 * 演示/开发用的自动登录凭据。
 *
 * 后端只有在监听环回且库里一个账号都没有时才会预置 admin/1234（见
 * app/api/routes_auth.seed_loopback_admin），所以这份默认值不会替真部署打开后门；
 * 要给团队用时把 VITE_DEV_AUTOLOGIN=false 写进 .env，登录页就是唯一的门。
 */
export const devAutoLogin =
  import.meta.env.VITE_DEV_AUTOLOGIN === "false"
    ? null
    : {
        username: (import.meta.env.VITE_DEV_USER as string) || "admin",
        password: (import.meta.env.VITE_DEV_PASS as string) || "1234",
      };
