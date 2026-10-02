"""MCP 工具面。

形状：**显式工具覆盖主干流程，两个通用出口覆盖长尾**。
后端 90 来个端点全写成 tool 会变成一份和 OpenAPI 平行的、必然漂移的清单；所以这里只把
「智能体真会按顺序调的那几十步」写成带说明的工具，其余一律走 `api_request`（配合
`api_catalog` 自查端点）。

三条贯穿所有工具的约定，写进 instructions 也写在这里：
1. 单卡互斥：本地实例一次只跑一个生成任务，派发前后端会让显存（可能停掉文本模型进程）。
   所以 `job_wait` 是阻塞的，出片可以是十几分钟量级。
2. 创作实体（项目/角色/场景/镜头/时间轴）在浏览器 IndexedDB 里，服务端没有 projects 表。
   工具能带上 `projectKey` 做软引用（媒体归组、版本历史、导出归档都靠它），但**读不到
   分镜表本身** —— 需要它时由调用方作为参数交进来。
3. scope：read 只能查；dispatch 能入队/派发；admin 能改实例、AI 后端、用户、目录。
   不够用时后端回 403，消息里会写该用哪一档重新发钥匙。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from .runtime import get_client


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def register_all(mcp: MCPServer) -> None:
    # ---------------------------------------------------------------- 运行状态

    @mcp.tool()
    async def health() -> dict:
        """后端存活检查：数据库是否接入、实例数量、媒体根目录。"""
        client = await get_client()
        return await client.get("/healthz")

    @mcp.tool()
    async def status() -> dict:
        """一屏看清全局：健康度 + 实例探活 + 队列积压 + 显存与磁盘 + GPU 让位状态。

        做任何生成动作之前先调它一次，比逐个问划算。
        """
        client = await get_client()
        out: dict[str, Any] = {}
        out["health"] = await client.get("/healthz")
        out["instances"] = await client.get("/api/instances")
        out["jobs"] = {
            "queued": await client.get("/api/jobs", params={"state": "queued", "limit": 200}),
            "running": await client.get("/api/jobs", params={"state": "running", "limit": 200}),
            "failed": await client.get("/api/jobs", params={"state": "failed", "limit": 20}),
        }
        for key, path in (("gpu", "/api/system/gpu"), ("storage", "/api/system/storage")):
            try:
                out[key] = await client.get(path)
            except Exception as exc:  # 无库模式或部分端点不可用：如实标注，不假装一切正常
                out[key] = {"unavailable": str(exc)}
        return out

    @mcp.tool()
    async def gpu_status() -> dict:
        """单卡仲裁器状态：当前谁占卡、文本模型进程是否被让开。"""
        client = await get_client()
        return await client.get("/api/system/gpu")

    @mcp.tool()
    async def gpu_restore() -> dict:
        """把被让开的本地文本模型进程拉回来（admin scope）。

        正常情况不需要人工调用：派发循环空队列时看门狗会自动恢复。
        """
        client = await get_client()
        return await client.post("/api/system/gpu/restore")

    @mcp.tool()
    async def system_paths() -> dict:
        """当前媒体/临时目录与 ffmpeg 位置。"""
        client = await get_client()
        return await client.get("/api/system/paths")

    @mcp.tool()
    async def system_set_paths(media: str | None = None, tmp: str | None = None, ffmpeg: str | None = None) -> dict:
        """改媒体/临时目录或 ffmpeg 路径（admin scope）。留空表示交回环境变量那份。"""
        client = await get_client()
        return await client.put("/api/system/paths", {"media": media, "tmp": tmp, "ffmpeg": ffmpeg})

    @mcp.tool()
    async def storage_report() -> dict:
        """磁盘与媒体库占用：行数、体积、孤儿文件。"""
        client = await get_client()
        return await client.get("/api/system/storage")

    @mcp.tool()
    async def storage_gc(dry_run: bool = True) -> dict:
        """清理孤儿媒体（admin scope）。默认 dry_run 只报告，确认过再 dry_run=false。"""
        client = await get_client()
        return await client.post("/api/system/gc", {"dryRun": dry_run})

    @mcp.tool()
    async def audit_log(limit: int = 50, action: str | None = None) -> list:
        """操作审计流水（admin scope）。"""
        client = await get_client()
        return await client.get("/api/system/audit", params={"limit": limit, "action": action})

    @mcp.tool()
    async def backup_commands() -> dict:
        """备份/恢复数据库的命令建议（admin scope）。"""
        client = await get_client()
        return await client.get("/api/system/backup")

    @mcp.tool()
    async def style_presets() -> list:
        """内置风格预设清单（画幅、光影、镜头风格一类）。"""
        client = await get_client()
        return await client.get("/api/styles")

    # ---------------------------------------------------------------- 生成实例

    @mcp.tool()
    async def instance_list() -> list:
        """列出 ComfyUI / RunningHub 实例：协议、位置、探活结果、熔断状态。apiKey 只回 apiKeySet。"""
        client = await get_client()
        return await client.get("/api/instances")

    @mcp.tool()
    async def instance_probe(instance_id: str) -> dict:
        """真连一次那台实例（会写探活结果），返回可用节点/权重摘要与错误。"""
        client = await get_client()
        return await client.post(f"/api/instances/{instance_id}/probe")

    @mcp.tool()
    async def instance_object_info(instance_id: str, class_type: str | None = None) -> dict:
        """取那台实例的 /object_info。给 class_type 就只取该节点定义。

        模型文件名一律从这里解析，别硬编码：同一份权重在不同机器上目录名与大小写都可能不同。
        """
        client = await get_client()
        return await client.get(f"/api/instances/{instance_id}/object-info", params={"class_type": class_type})

    @mcp.tool()
    async def instance_create(
        name: str,
        base_url: str,
        protocol: str = "comfy_native",
        placement: str = "local",
        ws_url: str | None = None,
        api_key: str | None = None,
        site: str = "cn",
        instance_type: str | None = None,
        retain_seconds: int | None = None,
        is_default: bool = False,
        local_output_root: str | None = None,
        tunnel_name: str | None = None,
    ) -> dict:
        """登记一台生成实例（admin scope）。protocol=comfy_native|rh_task，placement=local|cloud_self|cloud_runninghub。"""
        client = await get_client()
        body = {
            "name": name,
            "protocol": protocol,
            "placement": placement,
            "baseUrl": base_url,
            "wsUrl": ws_url,
            "apiKey": api_key,
            "site": site,
            "instanceType": instance_type,
            "retainSeconds": retain_seconds,
            "isDefault": is_default,
            "localOutputRoot": local_output_root,
            "tunnelName": tunnel_name,
        }
        return await client.post("/api/instances", {k: v for k, v in body.items() if v is not None})

    @mcp.tool()
    async def instance_update(instance_id: str, fields: dict) -> dict:
        """改一台实例的字段（admin scope）。fields 用 camelCase，如 {"baseUrl": "http://127.0.0.1:8188"}。"""
        client = await get_client()
        return await client.patch(f"/api/instances/{instance_id}", fields)

    @mcp.tool()
    async def instance_delete(instance_id: str) -> dict:
        """删除一台实例（admin scope）。"""
        client = await get_client()
        await client.delete(f"/api/instances/{instance_id}")
        return {"deleted": instance_id}

    # ---------------------------------------------------------------- 工作流库

    @mcp.tool()
    async def workflow_list() -> list:
        """内置模板 + 已导入工作流的总清单（含能否自动选中、验证状态）。"""
        client = await get_client()
        return await client.get("/api/workflows")

    @mcp.tool()
    async def workflow_get(workflow_id: str) -> dict:
        """一条工作流的详情。"""
        client = await get_client()
        return await client.get(f"/api/workflows/{workflow_id}")

    @mcp.tool()
    async def workflow_select(kind: str = "video", instance_id: str | None = None, slots: dict | None = None) -> dict:
        """问「这种任务会自动挑中哪条工作流」，只排序不建任务。slots 是信号（JSON 对象）。

        candidates 为空是真的没得选，会回落内置模板 —— 别当成命中。
        """
        client = await get_client()
        return await client.get(
            "/api/workflows/select",
            params={"kind": kind, "instance_id": instance_id, "slots": json.dumps(slots or {}, ensure_ascii=False)},
        )

    @mcp.tool()
    async def workflow_slots(workflow_id: str) -> list:
        """这条工作流暴露出来的参数槽（slot 寻址，如 6.text、115.strength）。填任务参数先看它。"""
        client = await get_client()
        return await client.get(f"/api/workflows/{workflow_id}/slots")

    @mcp.tool()
    async def workflow_models(workflow_id: str, instance_id: str | None = None) -> dict:
        """这条工作流需要哪些权重，以及那台实例上有没有。"""
        client = await get_client()
        return await client.get(f"/api/workflows/{workflow_id}/models", params={"instance_id": instance_id})

    @mcp.tool()
    async def workflow_validate(graph: dict, instance_id: str | None = None) -> dict:
        """校验一张 ComfyUI API 图：未知节点、缺权重、RH 专有节点、错误都会列出来（不当异常抛）。"""
        client = await get_client()
        return await client.post("/api/workflows/validate", {"graph": graph, "instance_id": instance_id})

    @mcp.tool()
    async def workflow_import(path: str, name: str | None = None, description: str | None = None, instance_id: str | None = None, priority: int = 100, tags: str | None = None) -> dict:
        """从本地 JSON 文件导入工作流（admin scope）。path 是 MCP 进程所在机器的文件路径。"""
        client = await get_client()
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"找不到文件：{p}")
        fields = {"name": name, "description": description, "instance_id": instance_id, "priority": str(priority), "tags": tags}
        return await client.upload("/api/workflows/import", p, fields={k: v for k, v in fields.items() if v is not None})

    @mcp.tool()
    async def workflow_rescan(workflow_id: str, instance_id: str | None = None) -> dict:
        """重新扫描一条工作流：重算槽位、缺权重、节点归属。"""
        client = await get_client()
        return await client.post(f"/api/workflows/{workflow_id}/rescan", params={"instance_id": instance_id})

    @mcp.tool()
    async def workflow_export(workflow_id: str, format: str = "api") -> dict:
        """导出工作流 JSON（format=api|ui）。"""
        client = await get_client()
        return await client.get(f"/api/workflows/{workflow_id}/export", params={"format": format})

    @mcp.tool()
    async def workflow_update(workflow_id: str, fields: dict) -> dict:
        """改工作流元数据：autoSelect / priority / description / tags。"""
        client = await get_client()
        return await client.patch(f"/api/workflows/{workflow_id}", fields)

    @mcp.tool()
    async def workflow_delete(workflow_id: str) -> dict:
        """删除一条工作流（admin scope）。"""
        client = await get_client()
        await client.delete(f"/api/workflows/{workflow_id}")
        return {"deleted": workflow_id}

    @mcp.tool()
    async def workflow_test(workflow_id: str, instance_id: str, slots: dict | None = None, project_key: str | None = None) -> dict:
        """拿给定参数在这台实例上试跑一次这条工作流（会真占显存）。"""
        client = await get_client()
        return await client.post(
            f"/api/workflows/{workflow_id}/test",
            {"instanceId": instance_id, "slots": slots or {}, "projectKey": project_key},
        )

    # ---------------------------------------------------------------- 文本模型

    @mcp.tool()
    async def llm_backends(scope: str | None = None) -> list:
        """列出文本模型后端（local=本机 llama.cpp/Ollama，cloud=托管）。"""
        client = await get_client()
        return await client.get("/api/llm/backends", params={"scope": scope})

    @mcp.tool()
    async def llm_backend_probe(backend_id: str) -> dict:
        """探一次后端可用性并回能力（是否吃图、上下文长度、可用模型）。"""
        client = await get_client()
        return await client.post(f"/api/llm/backends/{backend_id}/probe")

    @mcp.tool()
    async def llm_backend_create(fields: dict) -> dict:
        """新增文本后端（admin scope）。必填 name/scope/kind/base_url，可选 model、apiKey、chatPath、timeoutSeconds。"""
        client = await get_client()
        return await client.post("/api/llm/backends", fields)

    @mcp.tool()
    async def llm_backend_update(backend_id: str, fields: dict) -> dict:
        """改文本后端配置（admin scope）。"""
        client = await get_client()
        return await client.patch(f"/api/llm/backends/{backend_id}", fields)

    @mcp.tool()
    async def llm_backend_delete(backend_id: str) -> dict:
        """删除文本后端（admin scope）。"""
        client = await get_client()
        await client.delete(f"/api/llm/backends/{backend_id}")
        return {"deleted": backend_id}

    @mcp.tool()
    async def llm_models(backend_id: str) -> list:
        """那个后端现在能用哪些模型名。"""
        client = await get_client()
        return await client.get("/api/llm/models", params={"backend_id": backend_id})

    @mcp.tool()
    async def llm_local_scan() -> dict:
        """扫本机常见的推理端口，看有没有已经在跑但没登记的模型服务。"""
        client = await get_client()
        return await client.get("/api/llm/local-scan")

    @mcp.tool()
    async def llm_pull(backend_id: str, model: str) -> dict:
        """让那个后端拉一个模型（Ollama 有效；admin scope）。"""
        client = await get_client()
        return await client.post(f"/api/llm/backends/{backend_id}/pull", {"model": model})

    @mcp.tool()
    async def llm_defaults() -> dict:
        """每个用途当前用哪个后端、哪个模型。"""
        client = await get_client()
        return await client.get("/api/llm/defaults")

    @mcp.tool()
    async def llm_set_default(purpose: str, backend_id: str | None = None, model: str | None = None) -> dict:
        """设定某个用途的默认后端/模型（admin scope）。purpose ∈ script_parse|storyboard|visualize|h3_prompt|script_write|script_chat|embed。"""
        client = await get_client()
        return await client.post(f"/api/llm/backends/{backend_id}/default" if backend_id else "/api/llm/defaults", {"purpose": purpose, "backendId": backend_id, "model": model})

    @mcp.tool()
    async def llm_run(
        purpose: str,
        input: str,
        backend_id: str | None = None,
        model: str | None = None,
        script: str | None = None,
        messages: list | None = None,
        mode: str | None = None,
        target_sec: int | None = None,
        pace: str | None = None,
        duration_sec: float | None = None,
        aspect: str | None = None,
        style: str | None = None,
    ) -> dict:
        """跑一个文本用途。purpose：
        script_parse 剧本拆解 / storyboard 分镜规划 / visualize 视觉化 / h3_prompt 出片提示词 /
        script_write 正文写作 / script_chat 带历史的对话改稿 / embed 向量化。input 是这一轮的输入。

        本地 27B 量级很慢（拆解约 80s、分镜约 110s），且单槽串行 —— 别并发提交同一台机器。
        script_chat 用 script + messages；h3_prompt 用 mode/duration_sec/aspect/style。
        """
        client = await get_client()
        body = {
            "purpose": purpose,
            "input": input,
            "backendId": int(backend_id) if backend_id else None,
            "model": model,
            "script": script,
            "messages": messages,
            "mode": mode,
            "targetSec": target_sec,
            "pace": pace,
            "durationSec": duration_sec,
            "aspect": aspect,
            "style": style,
        }
        return await client.post("/api/llm/run", {k: v for k, v in body.items() if v is not None})

    # ---------------------------------------------------------------- 任务队列

    @mcp.tool()
    async def job_plan(jobs: list) -> dict:
        """入队前的预检：会给每个任务挑工作流、检查实例与显存、列出会挡路的原因。批量提交前先看它。

        jobs 每项形如 {"template":"auto","kind":"image","slots":{...},"projectKey":"..."}。
        """
        client = await get_client()
        return await client.post("/api/jobs/plan", {"jobs": jobs})

    @mcp.tool()
    async def job_submit(
        kind: str = "image",
        template: str | None = None,
        slots: dict | None = None,
        instance_id: str | None = None,
        workflow_id: int | None = None,
        graph: dict | None = None,
        models: dict | None = None,
        title: str | None = None,
        project_key: str | None = None,
        priority: int = 100,
        meta: dict | None = None,
    ) -> dict:
        """提交一个生成任务进队列。template="auto" 让工作流库按这次任务挑；直接给 graph 是高级用法。

        slots 的键是工作流槽位（先调 workflow_slots）。models 的值必须是那台实例
        object_info 真报出来的文件名，服务端不做就近凑匹配。
        meta 用 {"role":"character_ref","refId":"char-3"} 这类标签，产物才归得回资产。
        """
        client = await get_client()
        body = {
            "kind": kind,
            "template": template,
            "slots": slots,
            "instanceId": instance_id,
            "workflowId": workflow_id,
            "graph": graph,
            "models": models,
            "title": title,
            "projectKey": project_key,
            "priority": priority,
            "meta": meta,
        }
        return await client.post("/api/jobs", {k: v for k, v in body.items() if v is not None})

    @mcp.tool()
    async def job_batch_submit(jobs: list) -> dict:
        """一次提交多个任务（上限 100）。单卡机器会排队，不是并发。"""
        client = await get_client()
        return await client.post("/api/jobs/batch", {"jobs": jobs})

    @mcp.tool()
    async def job_list(state: str | None = None, project_key: str | None = None, limit: int = 100) -> list:
        """列任务。state ∈ queued|dispatching|running|succeeded|failed|canceled。"""
        client = await get_client()
        return await client.get("/api/jobs", params={"state": state, "project_key": project_key, "limit": limit})

    @mcp.tool()
    async def job_get(job_id: str) -> dict:
        """一个任务的现状：状态、进度、用的哪条工作流、填图与换权重的说明、产物 mediaId。"""
        client = await get_client()
        return await client.get(f"/api/jobs/{job_id}")

    @mcp.tool()
    async def job_wait(job_id: str, timeout_sec: float = 1800, interval_sec: float = 5) -> dict:
        """阻塞等到任务终态再返回（含 outputs 路径）。

        这会占住你一次工具调用很久：出片在本机是分钟到十几分钟量级，且派发前后端可能
        先让显存（停掉本地文本模型）。长任务建议 job_submit 后先做别的事，再用 job_get 抽查。
        """
        client = await get_client()
        return await client.wait_for_job(job_id, timeout_s=timeout_sec, interval_s=interval_sec)

    @mcp.tool()
    async def job_cancel(job_id: str) -> dict:
        """取消一个任务（dispatch scope）。"""
        client = await get_client()
        return await client.post(f"/api/jobs/{job_id}/cancel")

    @mcp.tool()
    async def job_retry(job_id: str) -> dict:
        """重试一个失败任务（dispatch scope）。会重新选图，不复用上次那张已污染的图。"""
        client = await get_client()
        return await client.post(f"/api/jobs/{job_id}/retry")

    @mcp.tool()
    async def job_update(job_id: str, priority: int | None = None, title: str | None = None) -> dict:
        """改队列里任务的优先级或标题。"""
        client = await get_client()
        return await client.patch(f"/api/jobs/{job_id}", {k: v for k, v in {"priority": priority, "title": title}.items() if v is not None})

    # ---------------------------------------------------------------- 媒体与版本

    @mcp.tool()
    async def media_list(project_key: str | None = None, role: str | None = None, ref_id: str | None = None, kind: str | None = None, ids: str | None = None, limit: int = 200, include_trashed: bool = False) -> list:
        """列媒体行。role/refId 就是「这是谁的哪张」，与 job_submit 的 meta 对应。ids 传逗号分隔。"""
        client = await get_client()
        params = {
            "project_key": project_key,
            "role": role,
            "ref_id": ref_id,
            "kind": kind,
            "ids": ids,
            "limit": limit,
        }
        rows = await client.get("/api/media", params=params)
        if include_trashed:
            rows = rows + await client.get("/api/trash", params={"project_key": project_key, "limit": limit})
        return rows

    @mcp.tool()
    async def media_location(media_id: int) -> dict:
        """产物在哪：服务端绝对路径 + 可直接打开的 raw URL（要带 Authorization）。"""
        client = await get_client()
        rows = await client.get("/api/media", params={"ids": str(media_id), "limit": 1})
        row = rows[0] if rows else {}
        return {
            "mediaId": media_id,
            "path": row.get("path"),
            "kind": row.get("kind"),
            "role": row.get("role"),
            "refId": row.get("refId"),
            "rawUrl": f"{client.server}/api/media/{media_id}/raw",
            "downloadUrl": f"{client.server}/api/media/{media_id}/download",
        }

    @mcp.tool()
    async def media_download(media_id: int, dest: str, trashed: bool = False) -> dict:
        """把产物下载到 MCP 进程所在机器的文件路径。"""
        client = await get_client()
        out = await client.download(f"/api/media/{media_id}/raw", Path(dest), params={"trashed": 1} if trashed else None)
        return {"mediaId": media_id, "savedTo": str(out)}

    @mcp.tool()
    async def media_upload(path: str, project_key: str | None = None, role: str = "ref_image", ref_id: str | None = None) -> dict:
        """上传本地文件做素材（参考图、底色视频等），返回 media 行。

        role 是有讲究的标签：喂给出片链的参考帧要用后端认的值，别随手编。
        """
        client = await get_client()
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"找不到文件：{p}")
        fields = {"role": role}
        if project_key:
            fields["project_key"] = project_key
        if ref_id:
            fields["ref_id"] = ref_id
        return await client.upload("/api/media/upload", p, fields=fields)

    @mcp.tool()
    async def media_versions(project_key: str, role: str | None = None, ref_id: str | None = None) -> dict:
        """同一张卡片的图片/视频版本历史（V1..Vn，含哪个是当前版）。"""
        client = await get_client()
        return await client.get("/api/media-versions", params={"project_key": project_key, "role": role, "ref_id": ref_id})

    @mcp.tool()
    async def media_trash(media_id: int) -> dict:
        """把一个产物送进生成回收站（软删，100 天内可恢复）。服务端隐藏该行的所有读路径立刻生效。"""
        client = await get_client()
        return await client.delete(f"/api/media/{media_id}")

    @mcp.tool()
    async def media_restore(media_id: int) -> dict:
        """从回收站恢复一个产物。"""
        client = await get_client()
        return await client.post(f"/api/media/{media_id}/restore")

    @mcp.tool()
    async def media_purge(media_id: int, confirm: bool = False) -> dict:
        """彻底删除一个产物 —— 真删文件，不可恢复。必须传 confirm=True。"""
        if not confirm:
            raise RuntimeError("需要 confirm=True：彻底删除不可恢复")
        client = await get_client()
        await client.delete(f"/api/media/{media_id}/purge")
        return {"purged": media_id}

    @mcp.tool()
    async def trash_list(project_key: str | None = None, limit: int = 200) -> list:
        """生成回收站清单（媒体版本 + 剧本版本混合，靠 kind 字段区分）。"""
        client = await get_client()
        return await client.get("/api/trash", params={"project_key": project_key, "limit": limit})

    # ---------------------------------------------------------------- 剧本版本

    @mcp.tool()
    async def script_versions(project_key: str, limit: int = 50) -> dict:
        """剧本正文的版本历史（服务端是真源：is_current 即当前版）。"""
        client = await get_client()
        return await client.get("/api/script-versions", params={"project_key": project_key, "limit": limit})

    @mcp.tool()
    async def script_version_save(project_key: str, text: str, source: str = "agent", set_current: bool = True, backfill_from: dict | None = None) -> dict:
        """存一版剧本正文。首次可以给 backfill_from={"text":...,"writtenAt":...} 把用户早就写好的
        那版一起交上来（服务端一个事务里先插 V1 再插 V2，绝不让你分两次 POST）。"""
        client = await get_client()
        body = {
            "projectKey": project_key,
            "text": text,
            "source": source,
            "setCurrent": set_current,
            "backfillFrom": backfill_from,
        }
        return await client.post("/api/script-versions", {k: v for k, v in body.items() if v is not None})

    @mcp.tool()
    async def script_version_set_current(version_id: str) -> dict:
        """把某一版设为当前版（正文同时写回）。"""
        client = await get_client()
        return await client.post(f"/api/script-versions/{version_id}/current")

    @mcp.tool()
    async def script_version_trash(version_id: str) -> dict:
        """剧本版本进回收站。"""
        client = await get_client()
        return await client.delete(f"/api/script-versions/{version_id}")

    @mcp.tool()
    async def script_version_restore(version_id: str) -> dict:
        """从回收站恢复剧本版本。"""
        client = await get_client()
        return await client.post(f"/api/script-versions/{version_id}/restore")

    @mcp.tool()
    async def script_version_purge(version_id: str, confirm: bool = False) -> dict:
        """彻底删除一个剧本版本，不可恢复。必须 confirm=True。"""
        if not confirm:
            raise RuntimeError("需要 confirm=True：彻底删除不可恢复")
        client = await get_client()
        await client.delete(f"/api/script-versions/{version_id}/purge")
        return {"purged": version_id}

    @mcp.tool()
    async def project_trash(project_key: str, name: str | None = None, confirm: bool = False) -> dict:
        """项目被删时，把它的产物与剧本版本整批送进回收站。会动用户的既有产物，必须 confirm=True。"""
        if not confirm:
            raise RuntimeError("需要 confirm=True：这会整批软删该项目的产物与剧本版本")
        client = await get_client()
        return await client.post(f"/api/projects/{project_key}/trash", {"name": name})

    # ---------------------------------------------------------------- 解析与导出

    @mcp.tool()
    async def parse_script(path: str) -> dict:
        """上传剧本文件（txt/md/fountain）做结构化解析，返回角色/场景/镜头骨架。"""
        client = await get_client()
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"找不到文件：{p}")
        return await client.upload("/api/parse-script", p)

    @mcp.tool()
    async def export_merge(project_key: str, media_ids: list, title: str | None = None, reencode: bool = False) -> dict:
        """按给定顺序把若干视频合成一条成片（ffmpeg）。media_ids 来自 job 的 outputs。"""
        client = await get_client()
        return await client.post(f"/api/projects/{project_key}/export/merge", {"mediaIds": media_ids, "title": title, "reencode": reencode})

    @mcp.tool()
    async def export_pack(project_key: str, items: list, title: str | None = None) -> dict:
        """打包素材包。items 形如 [{"mediaId":1,"path":"characters/柳如霜.png"}]。"""
        client = await get_client()
        return await client.post(f"/api/projects/{project_key}/export/pack", {"items": items, "title": title})

    @mcp.tool()
    async def export_timeline(project_key: str, shots: list, format: str = "edl", title: str | None = None) -> dict:
        """按分镜表导出剪辑格式：format ∈ edl|xml|jianying。

        shots 每项 {"index":1,"title":"...","durationSec":5,"mediaId":123,"sceneName":"..."}。
        时间轴本身存在浏览器 IndexedDB，所以这里必须由调用方交进来 —— 服务端没有它。
        """
        client = await get_client()
        if format not in {"edl", "xml", "jianying"}:
            raise ValueError(f"不支持的格式：{format}（edl|xml|jianying）")
        return await client.post(f"/api/projects/{project_key}/export/{format}", {"shots": shots, "title": title})

    # ---------------------------------------------------------------- 组合流程

    @mcp.tool()
    async def storyboard_from_script(script_text: str, target_sec: int | None = None, pace: str | None = None, backend_id: str | None = None, model: str | None = None) -> dict:
        """两步串好：剧本拆解 → 分镜规划，一次返回结构化结果。

        本地 27B 单槽串行，这一条实测约 3 分钟（拆解 79s + 分镜 113s），中途别并发别的文本调用。
        结果只落在这里返回，不会自动写进用户的稿子 —— 写回要人在页面上确认。
        """
        client = await get_client()
        common = {k: v for k, v in {"backendId": backend_id, "model": model}.items() if v is not None}
        parsed = await client.post("/api/llm/run", {"purpose": "script_parse", "input": script_text, **common})
        board = await client.post(
            "/api/llm/run",
            {"purpose": "storyboard", "input": _dumps(parsed), "targetSec": target_sec, "pace": pace, **common},
        )
        return {"parse": parsed, "storyboard": board}

    @mcp.tool()
    async def generate_and_wait(jobs: list, timeout_sec: float = 3600) -> dict:
        """批量入队并等全部到终态，返回每个任务的产物路径。适合「这批素材一口气跑完」的活。

        单卡机器上是排队执行，不是并发；总时长会很长，请确认调用方愿意阻塞。
        """
        client = await get_client()
        created = await client.post("/api/jobs/batch", {"jobs": jobs})
        ids = [str(j.get("id") or j.get("jobId")) for j in (created.get("jobs") or []) if (j.get("id") or j.get("jobId"))]
        results = []
        for job_id in ids:
            try:
                results.append(await client.wait_for_job(job_id, timeout_s=timeout_sec))
            except Exception as exc:
                results.append({"jobId": job_id, "error": str(exc)})
        return {"accepted": created.get("accepted"), "rejected": created.get("errors") or [], "results": results}

    # ---------------------------------------------------------------- 通用出口

    @mcp.tool()
    async def api_catalog(path_contains: str | None = None, method: str | None = None) -> list:
        """列后端 OpenAPI 的端点（方法、路径、说明），用来找 `api_request` 该打哪条。

        显式工具没覆盖到的功能都在这里，别猜路径。
        """
        client = await get_client()
        spec = await client.get("/openapi.json", auth=False)
        out = []
        for path, ops in (spec.get("paths") or {}).items():
            for verb, op in ops.items():
                if verb not in {"get", "post", "patch", "put", "delete"}:
                    continue
                if path_contains and path_contains not in path:
                    continue
                if method and verb != method.lower():
                    continue
                out.append({"method": verb.upper(), "path": path, "summary": op.get("summary") or op.get("description") or "", "operationId": op.get("operationId")})
        return out

    @mcp.tool()
    async def api_request(method: str, path: str, body: dict | None = None, params: dict | None = None) -> Any:
        """直接打后端任意端点（显式工具没覆盖的长尾走这里）。

        规矩：查询参数用 snake_case（project_key / instance_id），请求体用 camelCase。
        写操作要 dispatch/admin scope，被 403 挡住时消息会告诉你缺哪一档。
        """
        client = await get_client()
        verb = method.upper()
        if verb not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
            raise ValueError(f"不支持的方法：{method}")
        return await client.request(verb, path, json_body=body, params=params)
