"""H3 Studio 的 MCP server 装配。

一个进程一个 MCPServer 实例；stdio 与 streamable-http 只是两种 transport
（见 serve.py），工具面完全一样，所以「本地智能体」和「远程智能体」看到的能力一致。
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .tools import register_all

VERSION = "0.1.0"

INSTRUCTIONS = """\
H3 Studio 是一台本机 AI 视频生成工作台：ComfyUI / RunningHub 出图出片，本地或云端文本模型做剧本与分镜。

怎么用它（按这个顺序最省事）：
1. status 看清现场：有没有可用实例、队列是否积压、显存够不够。
2. 文本侧：llm_defaults 看用途→后端映射，llm_run 或 storyboard_from_script 出结构。
3. 生成侧：workflow_select 问「这批任务会挑中哪条工作流」，job_plan 预检，job_submit / job_batch_submit 入队，job_get 抽查，job_wait 才阻塞到终态。
4. 产物：media_list / media_location / media_download；版本历史 media_versions、script_versions；删过的东西在 trash_list。
5. 导出：export_merge（给定 mediaId 顺序）/ export_timeline（分镜表要你自己交进来）。
6. 显式工具没覆盖的端点：api_catalog 查、api_request 打。

四条必须知道的约束：
- 一张 24GB 卡装不下「27B 文本模型 + 图像/视频模型」。派发本地生成任务前后端会自动让显存，可能把 llama-server 停掉再拉回；这段时间文本调用会返回 409，等它恢复即可，别硬重试。
- 生成任务串行。单卡上 job_batch_submit 是排队不是并发，出片实测分钟到十几分钟级；除非确实要等，否则不要用 job_wait 占住整轮。
- 创作实体（项目/角色/场景/镜头/时间轴）在浏览器 IndexedDB 里，服务端没有 projects 表。projectKey 只是软引用，用于产物归组、版本历史与导出归档；需要分镜表本身时由你作为参数交进来。
- AI 产物停在「待确认」是产品口径：任何写回用户既有稿子的动作，都要人在页面上确认，不要用工具绕过。

凭据分档：read 只查；dispatch 能入队与派发；admin 能改实例/AI 后端/用户/目录。
被 403 挡住时消息会写该用哪一档重新发钥匙（h3 token create --scope ...）。
"""


def build_server(name: str = "h3-studio") -> MCPServer:
    mcp = MCPServer(
        name=name,
        title="H3 Studio",
        description="AI 视频生成工作台的实例、工作流、任务队列、文本模型、产物与导出的统一入口。",
        version=VERSION,
        instructions=INSTRUCTIONS,
    )
    register_all(mcp)
    return mcp
