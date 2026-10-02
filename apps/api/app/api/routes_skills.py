"""技能库路由：新建、批量导入、改、删单条、清空整库。

技能 = 一段可复用的写法要求（名称 + 说明 + 正文 + 挂在哪个环节）。
它和工作流库是两件事：工作流管「这张图怎么算」，技能管「这段提示词按什么写法发给模型」。

正文留在服务端而不是浏览器：项目实体按 A 方案在 IndexedDB 里，但技能库是跨项目、
跨机器共享的东西 —— 前端只存 skillIds，提交那一刻由 /llm/run 与 /jobs 各自读正文拼进去，
所以改一次技能，所有挂着它的资产与镜头下次生成就都变了。
"""

from __future__ import annotations

import json
import re
import uuid as _uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import Field
from sqlalchemy import select

from ..db import get_session
from ..logging_setup import get_logger
from ..models import Skill
from ..security import admin_gate, login_gate
from .common import CamelModel
from .routes_auth import _audit
log = get_logger("api.skills")
router = APIRouter(tags=["skills"])

MAX_CONTENT_CHARS = 20_000
#: 导入认的文件类型：JSON 一份可以装多条；markdown/txt 一份一条（frontmatter 给元数据）
ACCEPTED_SUFFIX = (".json", ".md", ".markdown", ".txt")

STAGES = ("general", "script", "asset", "video")
STAGE_ALIASES = {
    "general": "general", "通用": "general", "all": "general",
    "script": "script", "剧本": "script", "编剧": "script",
    "asset": "asset", "资产": "asset", "角色": "asset", "场景": "asset", "画面": "asset",
    "video": "video", "视频": "video", "出片": "video", "镜头": "video",
}


class SkillBody(CamelModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    content: str | None = None
    stage: Literal["general", "script", "asset", "video"] | None = None
    tags: list[str] | None = None


class SkillCreate(SkillBody):
    name: str = Field(..., min_length=1, max_length=200)  # type: ignore[assignment]
    content: str = Field(..., min_length=1, max_length=MAX_CONTENT_CHARS)  # type: ignore[assignment]


def _bad(detail: str) -> HTTPException:
    return HTTPException(400, detail)


def _skill_out(row: Skill) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "uuid": row.uuid,
        "name": row.name,
        "description": row.description or "",
        "content": row.content,
        "stage": row.stage,
        "tags": list(row.tags or []),
        "origin": row.origin,
        "source": row.source,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else "",
    }


def _pick(raw: dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = raw.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _norm_stage(value: str) -> str:
    return STAGE_ALIASES.get(value.strip().lower(), "general")


def _norm_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[,，、;；|]", value)
    elif isinstance(value, list):
        parts = [str(x) for x in value]
    else:
        parts = []
    return [p.strip() for p in parts if p.strip()][:12]


def _parse_json(text_: str, fallback_name: str) -> tuple[list[dict[str, Any]], list[str]]:
    """一份 JSON 里可能是一条技能、一个数组，或 {"skills": [...]} 的整库导出。"""
    try:
        data = json.loads(text_)
    except json.JSONDecodeError as exc:
        return [], [f"不是合法 JSON：{exc.msg}（第 {exc.lineno} 行）"]
    if isinstance(data, dict) and isinstance(data.get("skills"), list):
        rows = data["skills"]
        lib_name = _pick(data, "name", "名称", "title") or fallback_name
        return [dict(r, name=_pick(r, "name", "名称", "title") or f"{lib_name} {i + 1}") for i, r in enumerate(rows) if isinstance(r, dict)], []
    if isinstance(data, dict):
        rows: list[Any] = [data]
    elif isinstance(data, list):
        rows = data
    else:
        return [], ["JSON 顶层既不是对象也不是数组，认不出技能条目"]
    out: list[dict[str, Any]] = []
    for i, r in enumerate(rows):
        if isinstance(r, str):
            # 一整段文本当一条技能：文件名是名字，正文就是它
            out.append({"name": fallback_name if i == 0 else f"{fallback_name} {i + 1}", "content": r})
            continue
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "name": _pick(r, "name", "名称", "title") or (fallback_name if i == 0 else f"{fallback_name} {i + 1}"),
                "description": _pick(r, "description", "说明", "desc", "summary"),
                "content": _pick(r, "content", "正文", "body", "instruction", "prompt", "text"),
                "stage": _pick(r, "stage", "环节", "scope", "use"),
                "tags": r.get("tags") or r.get("标签") or [],
            }
        )
    return out, []


_FRONTMATTER = re.compile(r"\A\s*---\s*\n(.*?)\n---\s*\n", re.S)


def _parse_markdown(text_: str, fallback_name: str) -> dict[str, Any]:
    """frontmatter 给元数据，正文就是发给模型的那段。Claude 风格的 SKILL.md 直接吃。"""
    meta: dict[str, str] = {}
    body = text_
    m = _FRONTMATTER.match(text_)
    if m:
        body = text_[m.end():]
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip().strip("\"'")
    heading = re.search(r"^#{1,3}\s*(.+?)\s*$", body, re.M)
    name = meta.get("name") or meta.get("名称") or (heading.group(1) if heading else "") or fallback_name
    content = body[heading.end():] if heading and heading.group(1) == name else body
    return {
        "name": name,
        "description": meta.get("description") or meta.get("说明") or meta.get("desc") or "",
        "content": content.strip(),
        "stage": meta.get("stage") or meta.get("环节") or "",
        "tags": _norm_tags(meta.get("tags") or meta.get("标签") or ""),
    }


def _parse_upload(name: str, raw: str) -> tuple[list[dict[str, Any]], list[str]]:
    lowered = name.lower()
    if not raw.strip():
        return [], [f"{name} 是空文件"]
    if lowered.endswith(".json"):
        return _parse_json(raw, name.removesuffix(".json").removesuffix("_api"))
    return [_parse_markdown(raw, name)], []


def _folder_stem(filename: str) -> str:
    """SKILL.md 这种「文件名固定、靠目录区分」的条目，名字要取它所在的目录。"""
    parts = [p for p in (filename or "").replace("\\", "/").split("/") if p]
    if len(parts) >= 2 and parts[-1].lower() in ("skill.md", "skills.md", "readme.md"):
        return parts[-2]
    return parts[-1] if parts else "未命名技能"


@router.get("/skills")
async def list_skills(
    stage: Literal["general", "script", "asset", "video"] | None = Query(None),
    _: Any = Depends(login_gate),
    session=Depends(get_session),
) -> list[dict[str, Any]]:
    """技能清单。前端弹窗按 stage 过滤：环节专属技能 + general（哪都能用）一起给。"""
    stmt = select(Skill).order_by(Skill.stage, Skill.name)
    if stage:
        stmt = stmt.where(Skill.stage.in_([stage, "general"]))
    return [_skill_out(r) for r in (await session.execute(stmt)).scalars().all()]


@router.post("/skills", status_code=201)
async def create_skill(
    body: SkillCreate,
    request: Request,
    actor: Any = Depends(admin_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    if (await session.scalar(select(Skill).where(Skill.name == body.name.strip()))) is not None:
        raise HTTPException(409, f"技能「{body.name.strip()}」已经在库里了，改个名字或直接编辑那一条")
    row = Skill(
        uuid=str(_uuid.uuid4()),
        name=body.name.strip(),
        description=(body.description or "").strip() or None,
        content=body.content.strip(),
        stage=body.stage or "general",
        tags=body.tags or [],
        origin="manual",
        created_by=getattr(actor, "id", None),
    )
    session.add(row)
    await session.flush()
    await _audit(
        session,
        user_id=getattr(actor, "id", None),
        actor=getattr(actor, "username", None),
        action="skill.create",
        target=f"{row.id} · {row.name}",
        request=request,
        detail={"stage": row.stage, "chars": len(row.content)},
    )
    await session.commit()
    await session.refresh(row)
    log.info("技能 %s 已新建（环节 %s，%d 字），操作人：%s", row.name, row.stage, len(row.content), getattr(actor, "username", "-"))
    return _skill_out(row)


@router.patch("/skills/{skill_id}")
async def update_skill(
    skill_id: str,
    body: SkillBody,
    request: Request,
    actor: Any = Depends(admin_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    row = await session.get(Skill, int(skill_id)) if skill_id.isdigit() else None
    if row is None:
        raise HTTPException(404, f"技能 {skill_id} 不存在")
    given = body.model_fields_set
    if "name" in given and body.name:
        clash = await session.scalar(select(Skill).where(Skill.name == body.name.strip(), Skill.id != row.id))
        if clash is not None:
            raise HTTPException(409, f"库里已经有一条叫「{body.name.strip()}」的技能")
        row.name = body.name.strip()
    if "description" in given:
        row.description = (body.description or "").strip() or None
    if "content" in given and body.content:
        row.content = body.content.strip()
    if body.stage is not None:
        row.stage = body.stage
    if body.tags is not None:
        row.tags = body.tags
    await session.commit()
    await session.refresh(row)
    return _skill_out(row)


@router.delete("/skills/{skill_id}", status_code=204)
async def delete_skill(skill_id: str, request: Request, actor: Any = Depends(admin_gate), session=Depends(get_session)) -> None:
    row = await session.get(Skill, int(skill_id)) if skill_id.isdigit() else None
    if row is None:
        raise HTTPException(404, f"技能 {skill_id} 不存在")
    name = row.name
    await session.delete(row)
    await _audit(
        session,
        user_id=getattr(actor, "id", None),
        actor=getattr(actor, "username", None),
        action="skill.delete",
        target=f"{skill_id} · {name}",
        request=request,
        detail={},
    )
    await session.commit()


@router.post("/skills/import")
async def import_skills(
    request: Request,
    files: list[UploadFile] = File(..., description="一份 JSON 可以装多条；markdown/txt 一份一条"),
    library: str | None = None,
    actor: Any = Depends(admin_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    """导入技能库：多文件（含整个文件夹）一次进来，按名字 upsert。

    认 .json / .md / .markdown / .txt：JSON 支持单条、数组、以及 {"skills": [...]} 那种整库导出；
    markdown 吃 frontmatter 的 name/description/tags/stage，正文就是发给模型的那段。
    同名就刷新那一条而不是再插一条 —— 重导一遍技能库不该让库里长出双胞胎。
    """
    lib_name = (library or "").strip() or None
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for f in files:
        fname = f.filename or "未命名文件"
        if not fname.lower().endswith(ACCEPTED_SUFFIX):
            errors.append({"file": fname, "reason": f"不收这种文件（只认 {', '.join(ACCEPTED_SUFFIX)}）"})
            continue
        raw = (await f.read()).decode("utf-8", errors="replace")
        stem = _folder_stem(fname)
        parsed, problems = _parse_upload(stem, raw)
        for p in problems:
            errors.append({"file": fname, "reason": p})
        for it in parsed:
            it["source"] = lib_name or stem
            items.append(it)

    created: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for it in items:
        name = str(it.get("name") or "").strip()[:200]
        content = str(it.get("content") or "").strip()
        if not name or not content:
            skipped.append({"name": name or "（没有名字）", "reason": "名称或正文是空的"})
            continue
        if len(content) > MAX_CONTENT_CHARS:
            skipped.append({"name": name, "reason": f"正文 {len(content)} 字，超过上限 {MAX_CONTENT_CHARS}"})
            continue
        row = await session.scalar(select(Skill).where(Skill.name == name))
        if row is None:
            row = Skill(
                uuid=str(_uuid.uuid4()),
                name=name,
                description=str(it.get("description") or "").strip()[:2000] or None,
                content=content,
                stage=_norm_stage(str(it.get("stage") or "")),
                tags=_norm_tags(it.get("tags")),
                origin="imported",
                source=str(it.get("source") or "")[:200] or None,
                created_by=getattr(actor, "id", None),
            )
            session.add(row)
            await session.flush()
            created.append(_skill_out(row))
        else:
            row.content = content
            row.description = str(it.get("description") or "").strip()[:2000] or None
            row.stage = _norm_stage(str(it.get("stage") or ""))
            row.tags = _norm_tags(it.get("tags"))
            row.origin = "imported"
            row.source = str(it.get("source") or "")[:200] or None
            await session.flush()
            updated.append(_skill_out(row))

    await _audit(
        session,
        user_id=getattr(actor, "id", None),
        actor=getattr(actor, "username", None),
        action="skill.import",
        target=lib_name or f"{len(files)} 个文件",
        request=request,
        detail={"files": len(files), "created": len(created), "updated": len(updated), "skipped": len(skipped), "errors": len(errors)},
    )
    await session.commit()
    log.info(
        "技能导入：文件 %d、新建 %d、刷新 %d、跳过 %d、读不动 %d，操作人：%s",
        len(files), len(created), len(updated), len(skipped), len(errors), getattr(actor, "username", "-"),
    )
    return {
        "files": len(files),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "counts": {"created": len(created), "updated": len(updated), "skipped": len(skipped), "errors": len(errors)},
    }


@router.post("/skills/clear")
async def clear_skills(
    request: Request,
    confirm: Literal["清空技能库"] = Query(..., description="必须原样填这五个字，防手滑"),
    actor: Any = Depends(admin_gate),
    session=Depends(get_session),
) -> dict[str, Any]:
    """清空整个技能库。只删技能定义，不动任何项目实体里存着的 skillIds。

    那些 id 之后会在生成时报「技能不存在」，界面上会把失效的选择亮出来 ——
    清空是有意动作，让它静默指空反而更糟。
    """
    del confirm  # 只靠参数存在性拦人，值本身不进任何逻辑
    rows = list((await session.execute(select(Skill))).scalars().all())
    names = [r.name for r in rows]
    for r in rows:
        await session.delete(r)
    await _audit(
        session,
        user_id=getattr(actor, "id", None),
        actor=getattr(actor, "username", None),
        action="skill.clear",
        target=f"{len(rows)} 条技能",
        request=request,
        detail={"names": names[:20]},
    )
    await session.commit()
    log.warning("整个技能库已清空（%d 条），操作人：%s", len(rows), getattr(actor, "username", "-"))
    return {"deleted": len(rows), "names": names}


async def load_skills(session, ids: list[str]) -> list[Skill]:
    """按前端给的 id 取出技能行，顺序照给定顺序（多条时的先后是有意义的）。

    找不到就当错误抛出去：静默少拼一段要求，用户看到的是「我明明勾了技能，模型没按它写」。
    """
    out: list[Skill] = []
    for sid in ids:
        row = await session.get(Skill, int(sid)) if str(sid).isdigit() else None
        if row is None:
            raise HTTPException(400, f"技能 {sid} 不在技能库里（可能已被删掉，重新选一次）")
        out.append(row)
    return out


def skills_block(rows: list[Skill], *, for_model: bool) -> str:
    """把技能正文拼成一段附加要求。

    for_model=True 给文本模型（进 system，说清「与上面的规则同等必须遵守」）；
    =False 给图/视频模型（并进提示词槽，措辞要短，模型不吃解释性话术）。
    """
    if not rows:
        return ""
    if for_model:
        head = "【本次要用的技能】以下是创作者为这次生成额外指定的写法要求，与上面的规则同样必须遵守；和上面冲突时以技能为准。"
        body = "\n\n".join(f"◇ 技能「{r.name}」：{(r.description.strip() + chr(10)) if r.description else ''}{r.content.strip()}" for r in rows)
        return f"{head}\n\n{body}"
    body = "\n".join(f"- {r.name}：{r.content.strip()}" for r in rows)
    return f"【技能要求】\n{body}"


def skill_names(rows: list[Skill]) -> list[str]:
    return [r.name for r in rows]


#: 一次生成能挂多少技能正文。技能是给模型的附加要求，不是第二份稿子：本机 27B 光导演方法论
#: 就吃掉 2-3k token 预填充，图/视频那边提示词过长也只是让文本编码器多嚼一会儿。
#: 超了要人来取舍，绝不默默截断 —— 截掉的正是用户以为生效的那几句。
MAX_BLOCK_CHARS = 6000


async def resolve_block(session, ids: list[str] | None, *, for_model: bool, limit: int = MAX_BLOCK_CHARS) -> tuple[str, list[str]]:
    """读库 → (附加要求文本, 技能名清单)。没选技能就是 ("", [])。

    /llm/run 用 for_model=True（进 system），/jobs 用 False（并进提示词槽）：
    两种措辞差在图/视频模型不吃解释性话术，而文本模型要知道这些要求和它的规则同级。
    名字清单一起返回是给任务 meta 用的 —— 事后可见「这条任务当时挂了哪几条技能」。
    """
    if not ids:
        return "", []
    rows = await load_skills(session, ids)
    total = sum(len(r.content) for r in rows)
    if total > limit:
        raise HTTPException(
            400,
            f"选中的 {len(rows)} 条技能正文合计 {total} 字，超过单次上限 {limit} 字。"
            "少勾几条，或把技能正文压成真正要遵守的那几句。",
        )
    return skills_block(rows, for_model=for_model), skill_names(rows)
