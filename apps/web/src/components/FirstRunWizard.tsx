import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Field, Input, Modal } from "./ui";
import { useApi } from "../lib/apiClient";
import type { ProbeReport } from "../lib/types";

const DONE_KEY = "h3studio.firstRun.dismissedAt";
const LOCAL_DEFAULT = "http://127.0.0.1:8188";

/** 地址里的主机是不是这台机器本身 —— 决定 placement，进而决定要不要走单卡仲裁与显存闸门 */
function isLoopback(url: string): boolean {
  try {
    const h = new URL(url).hostname;
    return h === "127.0.0.1" || h === "localhost" || h === "::1" || h === "[::1]";
  } catch {
    return false;
  }
}

/**
 * 首启配实例。
 *
 * 为什么要有它：装完机界面是空的，而后端没有实例就只会一直报「找不到可用实例」——
 * 对第一次跑这个工具的人来说，那和「装坏了」没有区别。这里只解决一件事：
 * 把「本机有没有已经跑着的 ComfyUI」这个问题问清楚，并且**探到才存**。
 *
 * 不做的事：不猜地址、不把探测失败当成成功、不做假进度条。
 * 探不到就照实说，并留一条「以后再说」—— 生成之外的功能（剧本/分镜/时间轴）本来就能用。
 */
export function FirstRunWizard() {
  const api = useApi();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("本机 ComfyUI");
  const [baseUrl, setBaseUrl] = useState(LOCAL_DEFAULT);
  const [apiKey, setApiKey] = useState("");
  const [outputRoot, setOutputRoot] = useState("");
  const [report, setReport] = useState<ProbeReport | null>(null);
  const [busy, setBusy] = useState<"" | "probe" | "save">("");
  const [error, setError] = useState<string | null>(null);

  const loopback = useMemo(() => isLoopback(baseUrl), [baseUrl]);

  useEffect(() => {
    // 原型模式（mock）不弹：那套数据是假的，配实例没有意义
    if (import.meta.env.VITE_USE_MOCK !== "false") return;
    if (window.localStorage.getItem(DONE_KEY)) return;
    let alive = true;
    api.instances
      .list()
      .then((list) => {
        if (alive && list.length === 0) setOpen(true);
      })
      .catch(() => {
        // 后端还没起来（首次 initdb 期间会这样）—— 不弹也不报错，下次进页面再问
      });
    return () => {
      alive = false;
    };
  }, [api]);

  function dismiss() {
    window.localStorage.setItem(DONE_KEY, new Date().toISOString());
    setOpen(false);
  }

  async function probe() {
    setBusy("probe");
    setError(null);
    setReport(null);
    try {
      setReport(await api.instances.dryProbe({ protocol: "comfy_native", baseUrl: baseUrl.trim(), apiKey: apiKey.trim() || undefined }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function save() {
    setBusy("save");
    setError(null);
    try {
      await api.instances.create({
        name: name.trim() || "本机 ComfyUI",
        protocol: "comfy_native",
        placement: loopback ? "local" : "cloud_self",
        baseUrl: baseUrl.trim(),
        apiKey: apiKey.trim() || null,
        localOutputRoot: outputRoot.trim() || null,
        isDefault: true,
      });
      dismiss();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  const native = report?.native;

  return (
    <Modal
      open={open}
      onClose={dismiss}
      title="接一台出图/出片的机器"
      width={640}
      footer={
        <>
          <Button variant="ghost" onClick={dismiss}>
            以后再说
          </Button>
          <Button variant="primary" onClick={save} loading={busy === "save"} disabled={!report?.ok}>
            保存并设为默认
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-note leading-relaxed text-ink-dim">
          H3 Studio 自己不出图。要出关键帧和成片，得给它一台 ComfyUI：本机跑着的、局域网里另一台的、或
          RunningHub 的原生代理地址都行。下面这个探测**只试连通性，不会改那台机器的任何东西**。
        </p>

        <Field label="ComfyUI 地址" hint="默认探本机 8188；远程就填 http://<内网地址>:8188，RunningHub 填 https://www.runninghub.cn/proxy/<apiKey>">
          <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} spellCheck={false} />
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label="apiKey" hint="本机 ComfyUI 留空">
            <Input value={apiKey} onChange={(e) => setApiKey(e.target.value)} type="password" spellCheck={false} placeholder="仅远程需要" />
          </Field>
          <Field label="实例名" hint="显示在队列与任务上">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
        </div>

        {loopback && (
          <Field label="本机 output 目录" hint="可选。填了就直接读盘取产物，省一次下载；不知道就留空">
            <Input value={outputRoot} onChange={(e) => setOutputRoot(e.target.value)} spellCheck={false} placeholder="C:\ComfyUI\output" />
          </Field>
        )}

        <div className="flex items-center gap-2">
          <Button onClick={probe} loading={busy === "probe"}>
            探测这台机器
          </Button>
          <Badge tone={loopback ? "good" : "neutral"}>{loopback ? "本机 · 走单卡仲裁" : "远程 · 不让卡"}</Badge>
        </div>

        {error && (
          <div role="alert" className="rounded-ctl border border-state-fail/45 bg-state-fail/10 px-2.5 py-2 text-note text-state-fail">
            {error}
          </div>
        )}

        {report && (
          <div className="rounded-ctl border border-rule-soft bg-slate px-2.5 py-2">
            {report.ok ? (
              <div className="space-y-1 text-note text-ink-dim">
                <p className="font-medium text-ink">连上了，可以保存。</p>
                <p className="mono text-caption text-ink-mute">
                  {native?.comfyVersion ? `ComfyUI ${native.comfyVersion}` : "ComfyUI"} · {native?.nodeCount ?? "?"} 个节点 ·{" "}
                  {native?.gpu || "GPU 未知"} · 显存 {native?.vramFreeGb ?? "?"}/{native?.vramTotalGb ?? "?"} GB
                </p>
                {!!native?.missingModels?.length && (
                  <p className="text-caption text-ink-mute">这台机器上还缺这些权重：{native.missingModels.join("、")}</p>
                )}
              </div>
            ) : (
              <div className="space-y-1 text-note text-ink-dim">
                <p className="text-state-fail">没连上：{report.error || "无响应"}</p>
                <ul className="list-disc space-y-0.5 pl-4 text-caption text-ink-mute">
                  {(report.hints?.length ? report.hints : ["确认那台机器上 ComfyUI 真的在跑，并且监听的是局域网地址而不是 127.0.0.1"]).map((h) => (
                    <li key={h}>{h}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        <p className="text-caption leading-snug text-ink-mute">
          这台机器上没有能跑的 NVIDIA 卡（比如 Apple Silicon）时，本机这一项注定探不通 —— 那就连远程的
          ComfyUI 或 RunningHub。以后要改，去「设置 → 服务与工作流」。
        </p>
      </div>
    </Modal>
  );
}
