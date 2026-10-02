/**
 * 口令规则的前端镜像，判定口径与后端 app/security.py 的 password_policy_error 一致。
 *
 * 这里的拦截只省一次往返，真正的边界在后端：改这两行不会让任何人少被拒一次。
 * 上一代表演口令 1234 也在拒绝名单里 —— 后端环回启动会把还停在 1234 的演示账号
 * 升到当前演示口令，谁都不该主动把口令设成一个会被静默换掉的值。
 */
export const MIN_PASSWORD_LEN = 4;

const BLOCKED = ["password", "1234567890", "admin123", "h3studio1", "comfyui123", "iloveyou1", "1234"];

export function passwordProblems(pw: string): string[] {
  const out: string[] = [];
  if (pw.length < MIN_PASSWORD_LEN) out.push(`至少 ${MIN_PASSWORD_LEN} 位，现在 ${pw.length} 位`);
  if (BLOCKED.includes(pw.toLowerCase())) out.push("命中常见弱口令表，换一个");
  if (pw !== pw.trim()) out.push("开头或结尾有空格");
  return out;
}
