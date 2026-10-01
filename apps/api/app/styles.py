"""视觉风格表。

manga-studio 的选择器有 15 项，但它的提示词表只写了 6 项，其余靠
`VISUAL_STYLE_PROMPTS[style] || style` 回落成风格 key 本身 —— 等于那 9 项
选了就等于没选。这里把 15 项补齐，前端拼装用的是同一份文案。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class VisualStyle:
    key: str
    name: str
    prompt: str
    emoji: str = ""
    desc: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "name": self.name, "promptZh": self.prompt, "emoji": self.emoji, "desc": self.desc}


VISUAL_STYLES: list[VisualStyle] = [
    VisualStyle("anime", "2D日漫", "Japanese anime style, cel-shaded, clean line art, vibrant colors, expressive eyes, dynamic poses, Makoto Shinkai quality", "🌟", "日式动漫风格，线条感强"),
    VisualStyle("2d-guoman", "2D国漫", "Chinese donghua 2D style, elegant flowing line art, ink-wash influenced coloring, ornate costume detail, cinematic composition", "🎨", "中国风 2D 国漫"),
    VisualStyle("2d-otome", "2D乙女", "otome game illustration style, soft pastel palette, delicate sparkle effects, slender elegant characters, romantic lighting", "💗", "乙女向 2D 风格，柔美细腻"),
    VisualStyle("2d-korean", "2D韩漫", "Korean manhwa webtoon style, sharp digital line art, tall stylized proportions, high contrast shading, glossy color rendering", "🇰🇷", "韩式漫画风格"),
    VisualStyle("2d-korean-urban", "2D韩漫都市", "modern Korean webtoon style, urban night scenery, neon reflections, fashionable characters, clean digital flat shading", "🏙️", "韩式都市漫画风"),
    VisualStyle("3d-animation", "3D卡通", "high-quality 3D CGI animation, Pixar/DreamWorks style, subsurface scattering, detailed textures, stylized characters", "👾", "3D 卡通 / 皮克斯风格"),
    VisualStyle("3d-xianxia", "3D仙侠", "3D xianxia fantasy CG style, flowing immortal robes, luminous spell VFX, floating mountains and sea of clouds, epic volumetric lighting", "🐉", "3D 仙侠玄幻风，国风山水与法术特效"),
    VisualStyle("3d-guofeng", "3D国风", "3D Chinese historical rendering, carved wooden palaces, silk and lacquer detail, lantern light, ink-inspired atmosphere", "🏯", "3D 中国古风场景"),
    VisualStyle("cyberpunk", "CG赛博朋克", "cyberpunk aesthetic, neon-lit, rain-soaked streets, holographic displays, high-tech low-life, Blade Runner style", "🌌", "高科技赛博朋克风"),
    VisualStyle("cg", "CG风格", "photoreal CG render, ray-traced global illumination, physically based materials, cinematic color grading, ultra detailed", "🖼️", "CG 渲染艺术风格"),
    VisualStyle("gongbi", "工笔画", "traditional Chinese gongbi brush painting, meticulous mineral pigment layers, silk texture, refined outlines, restrained elegant palette", "🖌️", "传统工笔画艺术风"),
    VisualStyle("live-action", "写实电影感", "photorealistic, cinematic film quality, real human actors, professional cinematography, natural lighting, 8K resolution", "🎬", "超写实电影/电视剧风格"),
    VisualStyle("realistic-urban", "写实都市", "realistic contemporary urban photography, natural daylight, candid framing, detailed street texture, shallow depth of field", "🌆", "写实都市场景"),
    VisualStyle("realistic", "写实通用", "photorealistic, natural lighting, physically accurate materials, fine detail, professional photography", "🎞️", "通用写实风格"),
    VisualStyle("oil-painting", "油画风格", "oil painting style, visible brushstrokes, rich textures, classical art composition, museum quality fine art", "🖼️", "油画笔触与古典构图"),
]

BY_KEY = {s.key: s for s in VISUAL_STYLES}


def prompt_for(key: str, fallback: str = "") -> str:
    style = BY_KEY.get(key)
    if style is not None:
        return style.prompt
    # 「其他（自定义）」：用户写的就是提示词本身，不再套模板
    return fallback or key
