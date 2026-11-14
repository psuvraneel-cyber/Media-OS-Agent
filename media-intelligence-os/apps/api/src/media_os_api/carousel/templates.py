"""HTML/CSS slide templates (1080×1350, Instagram 4:5) in the brand's palette and fonts.

Pure string building so it can be unit-tested without a browser. Every piece of model text is
HTML-escaped. Text boxes carry `data-fit` so the renderer can shrink type to fit and report any box
that still overflows (a QA failure).
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from media_os_api.carousel.brand_kit import BrandKit
from media_os_api.carousel.schema import Slide, SlideRole
from media_os_api.content.taxonomy import Language

WIDTH, HEIGHT = 1080, 1350

FONT_FACES = (
    ("Anek Bangla", 600, "anek-bangla-bengali-600-normal.woff2", "U+0980-09FF"),
    ("Anek Bangla", 800, "anek-bangla-bengali-800-normal.woff2", "U+0980-09FF"),
    ("Anek Bangla", 600, "anek-bangla-latin-600-normal.woff2", "U+0000-00FF,U+2000-206F"),
    ("Anek Bangla", 800, "anek-bangla-latin-800-normal.woff2", "U+0000-00FF,U+2000-206F"),
    ("Hind Siliguri", 500, "hind-siliguri-bengali-500-normal.woff2", "U+0980-09FF"),
    ("Hind Siliguri", 700, "hind-siliguri-bengali-700-normal.woff2", "U+0980-09FF"),
    ("Hind Siliguri", 500, "hind-siliguri-latin-500-normal.woff2", "U+0000-00FF,U+2000-206F"),
    ("Hind Siliguri", 700, "hind-siliguri-latin-700-normal.woff2", "U+0000-00FF,U+2000-206F"),
)


@dataclass(frozen=True)
class SlideContext:
    index: int  # 1-based
    total: int
    language: Language
    image_uri: str | None  # file:// URI of the visual, if any
    image_kind: str | None  # CARD | SCREEN
    icon_uri: str | None
    font_base_uri: str  # file:///app/fonts/


def _fonts_css(base: str) -> str:
    return "\n".join(
        f"@font-face{{font-family:'{family}';font-weight:{weight};font-display:block;"
        f"src:url('{base}{file}') format('woff2');unicode-range:{rng};}}"
        for family, weight, file, rng in FONT_FACES
    )


def _css(kit: BrandKit, ctx: SlideContext) -> str:
    p = kit.palette
    return f"""
{_fonts_css(ctx.font_base_uri)}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{WIDTH}px;height:{HEIGHT}px;overflow:hidden}}
body{{font-family:'Hind Siliguri','Anek Bangla',sans-serif;color:{p["ink"]};background:{p["tint"]};
 position:relative;-webkit-font-smoothing:antialiased}}
.bar{{position:absolute;left:0;right:0;top:0;height:22px;background:{p["red"]};
 border-bottom:8px solid {p["gold"]}}}
.dark{{background:radial-gradient(circle at 50% 30%,{p["red"]} 0%,{p["red_deep"]} 70%);
 color:{p["tint"]}}}
.dark .bar{{background:{p["red_deep"]}}}
.frame{{position:absolute;inset:44px;border:3px solid {p["gold"]};border-radius:6px;
 pointer-events:none}}
.dark .frame{{border-color:{p["gold_soft"]}66}}
.content{{position:absolute;left:96px;right:96px;top:120px;bottom:150px;display:flex;
 flex-direction:column;gap:26px}}
.center{{justify-content:center;text-align:center;align-items:center}}
.vcenter{{justify-content:center}}
.h-bn,.h-en{{font-family:'Anek Bangla',sans-serif;font-weight:800;line-height:1.18;
 color:{p["red"]};letter-spacing:-.5px}}
.dark .h-bn,.dark .h-en{{color:{p["gold_soft"]}}}
.h-bn{{font-size:84px;line-height:1.32}}
.h-en{{font-size:68px}}
.sub-en{{font-family:'Anek Bangla',sans-serif;font-weight:600;font-size:44px;line-height:1.25;
 color:{p["gold"]}}}
.dark .sub-en{{color:{p["tint"]}}}
.b{{font-size:40px;line-height:1.45;font-weight:500}}
.b-en{{font-size:34px;line-height:1.4;font-weight:500;color:#5b4a46}}
.dark .b-en{{color:{p["gold_soft"]}}}
.fit{{overflow:visible}}
.media{{flex:1;min-height:0;display:flex;align-items:center;justify-content:center}}
.media img{{max-width:100%;max-height:100%;object-fit:contain;border-radius:28px;
 box-shadow:0 24px 60px rgba(60,10,10,.28)}}
.media img.screen{{height:100%;aspect-ratio:1080/1560;object-fit:cover;object-position:50% 100%}}
.num{{width:84px;height:84px;border-radius:50%;background:{p["red"]};color:{p["tint"]};
 font-family:'Anek Bangla';font-weight:800;font-size:44px;display:flex;align-items:center;
 justify-content:center;flex:none}}
.row{{display:flex;gap:28px;align-items:flex-start}}
.pill{{margin-top:18px;display:inline-flex;align-items:center;gap:18px;padding:26px 46px;
 border-radius:999px;background:{p["tint"]};color:{p["red_deep"]};font-family:'Anek Bangla';
 font-weight:800;font-size:44px}}
.icon{{width:200px;height:200px;border-radius:44px;box-shadow:0 18px 50px rgba(0,0,0,.35)}}
.foot{{position:absolute;left:96px;right:96px;bottom:70px;display:flex;
 justify-content:space-between;align-items:center;font-family:'Anek Bangla';font-weight:600;
 font-size:30px;color:{p["red_deep"]}}}
.dark .foot{{color:{p["gold_soft"]}}}
.dots{{display:flex;gap:10px}}
.dots i{{width:14px;height:14px;border-radius:50%;background:{p["gold"]}55}}
.dots i.on{{background:{p["gold"]}}}
"""


def _texts(slide: Slide, language: Language, *, compact: bool, hook: bool = False) -> str:
    """Headlines always; bodies trimmed when space is tight (hook slide or a visual below)."""
    out: list[str] = []
    if language is not Language.EN and slide.headline_bn:
        out.append(f'<h1 class="h-bn fit" data-fit>{escape(slide.headline_bn)}</h1>')
    if slide.headline_en and language is not Language.BN:
        cls = "h-en" if language is Language.EN else "sub-en"
        out.append(f'<h2 class="{cls} fit" data-fit>{escape(slide.headline_en)}</h2>')
    if hook:
        return "\n".join(out)
    show_bn_body = language is not Language.EN and slide.body_bn
    show_en_body = slide.body_en and language is not Language.BN
    if compact and language is Language.BN_EN:
        show_en_body = False  # bilingual with a visual: the English headline carries it
    if show_bn_body:
        out.append(f'<p class="b fit" data-fit>{escape(slide.body_bn or "")}</p>')
    if show_en_body:
        cls = "b" if language is Language.EN else "b-en"
        out.append(f'<p class="{cls} fit" data-fit>{escape(slide.body_en or "")}</p>')
    return "\n".join(out)


def _footer(kit: BrandKit, ctx: SlideContext) -> str:
    dots = "".join(
        f'<i class="{"on" if i == ctx.index else ""}"></i>' for i in range(1, ctx.total + 1)
    )
    return (
        f'<div class="foot"><span>{escape(kit.name_bn)} · {escape(kit.name_en)}</span>'
        f'<span class="dots">{dots}</span></div>'
    )


def render_slide_html(kit: BrandKit, slide: Slide, ctx: SlideContext) -> str:
    dark = slide.role in (SlideRole.HOOK, SlideRole.CTA)
    if slide.role is SlideRole.CTA:
        icon = f'<img class="icon" src="{escape(ctx.icon_uri)}" alt="">' if ctx.icon_uri else ""
        cta = "Google Play-এ পাবেন" if ctx.language is Language.BN else "Get it on Google Play"
        body = (
            f'<div class="content center">{icon}{_texts(slide, ctx.language, compact=True)}'
            f'<div class="pill">▶ {escape(cta)}</div></div>'
        )
    elif slide.role is SlideRole.HOOK and not ctx.image_uri:
        texts = _texts(slide, ctx.language, compact=True, hook=True)
        body = f'<div class="content center">{texts}</div>'
    else:
        shot = " screen" if ctx.image_kind == "SCREEN" else ""
        media = (
            f'<div class="media"><img class="{shot.strip()}" src="{escape(ctx.image_uri)}" alt="">'
            "</div>"
            if ctx.image_uri
            else ""
        )
        number = (
            f'<div class="num">{ctx.index - 1}</div>'
            if slide.role is SlideRole.POINT and not ctx.image_uri
            else ""
        )
        texts = _texts(
            slide,
            ctx.language,
            compact=bool(ctx.image_uri),
            hook=slide.role is SlideRole.HOOK,
        )
        head = f'<div class="row">{number}<div>{texts}</div></div>' if number else texts
        center = "" if ctx.image_uri else " center" if slide.role is SlideRole.HOOK else " vcenter"
        body = f'<div class="content{center}">{head}{media}</div>'
    lang = "en" if ctx.language is Language.EN else "bn"
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        f"<style>{_css(kit, ctx)}</style></head>"
        f'<body class="{"dark" if dark else ""}"><div class="bar"></div><div class="frame"></div>'
        f"{body}{_footer(kit, ctx)}</body></html>"
    )
