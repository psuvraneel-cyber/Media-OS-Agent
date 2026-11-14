"""HTML/CSS slide templates (1080×1350, Instagram 4:5) — design v2, built to stop the scroll.

Design moves (all in the brand palette, all local, no network):
* Hook: huge type with *highlighted* key words, a festive marigold garland (wedding/puja/season),
  an alpona mandala glow, a claim-backed starburst sticker, a tilted hero card or a phone on a
  stage, and a swipe cue.
* Content: alternating cream / warm-gold / deep-red backgrounds for rhythm, giant ghost numerals,
  kicker labels, mandala corner ornaments, large product visuals on a two-tone stage.
* CTA: app icon flanked by fanned real card renders and a Google Play button.

Pure string building so it can be unit-tested without a browser. All model text is HTML-escaped
before `*emphasis*` is turned into <mark>. Text boxes carry `data-fit` for the renderer's auto-fit.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from html import escape

from media_os_api.carousel.brand_kit import BrandKit
from media_os_api.carousel.schema import EMPHASIS, Slide, SlideRole
from media_os_api.content.taxonomy import Language

WIDTH, HEIGHT = 1080, 1350
BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")
MARIGOLD = ("#f29c1f", "#f7c234", "#e8771a")
LEAF = "#2f7a3e"

FONT_FACES = (
    ("Anek Bangla", 600, "anek-bangla-bengali-600-normal.woff2", "U+0980-09FF"),
    ("Anek Bangla", 800, "anek-bangla-bengali-800-normal.woff2", "U+0980-09FF"),
    ("Anek Bangla", 600, "anek-bangla-latin-600-normal.woff2", "U+0000-00FF,U+2000-206F"),
    ("Anek Bangla", 800, "anek-bangla-latin-800-normal.woff2", "U+0000-00FF,U+2000-206F"),
    ("Hind Siliguri", 500, "hind-siliguri-bengali-500-normal.woff2", "U+0980-09FF"),
    ("Hind Siliguri", 700, "hind-siliguri-bengali-700-normal.woff2", "U+0980-09FF"),
    ("Hind Siliguri", 500, "hind-siliguri-latin-500-normal.woff2", "U+0000-00FF,U+2000-206F"),
    ("Hind Siliguri", 700, "hind-siliguri-latin-700-normal.woff2", "U+0000-00FF,U+2000-206F"),
    ("Galada", 400, "galada-bengali-400-normal.woff2", "U+0980-09FF"),
    ("Galada", 400, "galada-latin-400-normal.woff2", "U+0000-00FF,U+2000-206F"),
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
    festive: bool = False  # wedding / puja / seasonal: marigold garland on the hook
    card_uris: tuple[str, ...] = field(default_factory=tuple)  # real card renders for the CTA fan
    content_number: int = 0  # 1-based position among content slides (for the ghost numeral)


# ----------------------------------------------------------------------------- ornaments (SVG)


def _mandala(color: str, opacity: float) -> str:
    """Procedural alpona-style mandala: petal rings, circles and dot rings."""
    parts: list[str] = []
    for r, n, rx, ry in ((18, 8, 9, 4), (38, 12, 13, 5), (60, 16, 15, 6), (83, 24, 11, 4.5)):
        for k in range(n):
            a = 360 * k / n
            parts.append(
                f'<ellipse cx="0" cy="{-r}" rx="{ry}" ry="{rx}" transform="rotate({a:.1f})"/>'
            )
    for r in (28, 49, 71, 94):
        parts.append(f'<circle r="{r}"/>')
    dots = []
    for k in range(36):
        a = 2 * math.pi * k / 36
        dots.append(f'<circle cx="{98 * math.cos(a):.1f}" cy="{98 * math.sin(a):.1f}" r="1.6"/>')
    return (
        f'<svg viewBox="-100 -100 200 200" '
        f'style="opacity:{opacity}"><g fill="none" stroke="{color}" stroke-width="1.1">'
        f'{"".join(parts)}</g><g fill="{color}">{"".join(dots)}</g></svg>'
    )


def _garland(width: int = WIDTH) -> str:
    """A hanging marigold toran across the top of the slide."""
    swags, per = 4, 9
    seg = width / swags
    flowers: list[str] = []
    string: list[str] = []
    for s in range(swags):
        for j in range(per + 1):
            t = j / per
            x = s * seg + t * seg
            y = 18 + 70 * math.sin(math.pi * t)
            string.append(f"{x:.1f},{y:.1f}")
            if 0 < j < per or (s == 0 and j == 0):
                c = MARIGOLD[(s * per + j) % len(MARIGOLD)]
                flowers.append(
                    f'<ellipse cx="{x - 9:.1f}" cy="{y + 6:.1f}" rx="9" ry="4" fill="{LEAF}" '
                    f'transform="rotate(-30 {x:.1f} {y:.1f})"/>'
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="15" fill="{c}"/>'
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="8" fill="{MARIGOLD[1]}" opacity=".7"/>'
                )
        if s < swags - 1:
            x = (s + 1) * seg
            flowers.append(
                f'<line x1="{x:.1f}" y1="18" x2="{x:.1f}" y2="120" stroke="{MARIGOLD[2]}" '
                f'stroke-width="3"/><circle cx="{x:.1f}" cy="128" r="11" fill="{MARIGOLD[0]}"/>'
            )
    return (
        f'<svg class="garland" viewBox="0 0 {width} 150">'
        f'<polyline points="{" ".join(string)}" fill="none" stroke="#c9a14a" stroke-width="2"/>'
        f"{''.join(flowers)}</svg>"
    )


def _starburst(points: int = 18) -> str:
    coords = []
    for k in range(points * 2):
        r = 50 if k % 2 == 0 else 42
        a = math.pi * k / points
        coords.append(f"{50 + r * math.cos(a):.1f}% {50 + r * math.sin(a):.1f}%")
    return f"polygon({', '.join(coords)})"


# ----------------------------------------------------------------------------- CSS


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
body{{font-family:'Hind Siliguri','Anek Bangla',sans-serif;color:{p["ink"]};position:relative;
 -webkit-font-smoothing:antialiased;background:{p["tint"]}}}
body.cream{{background:radial-gradient(120% 90% at 85% 10%,#fff8f2 0%,{p["tint"]} 55%,
 #f7e6da 100%)}}
body.warm{{background:linear-gradient(160deg,#fff6e2 0%,{p["gold_soft"]} 100%)}}
body.dark{{background:radial-gradient(110% 80% at 50% 28%,#c41d28 0%,{p["red"]} 38%,
 {p["red_deep"]} 100%);color:{p["tint"]}}}
.frame{{position:absolute;inset:34px;border:2px solid {p["gold"]}88;border-radius:10px;
 pointer-events:none}}
.frame::after{{content:"";position:absolute;inset:10px;border:1px solid {p["gold"]}55;
 border-radius:6px}}
.dark .frame{{border-color:{p["gold_soft"]}66}}
.dark .frame::after{{border-color:{p["gold_soft"]}33}}
.garland{{position:absolute;left:0;top:0;width:100%;height:150px;z-index:3}}
.mandala{{position:absolute;pointer-events:none}}
.mandala svg{{width:100%;height:100%}}
.m-hook{{width:1240px;height:1240px;left:-80px;top:40px}}
.m-corner{{width:620px;height:620px;right:-230px;bottom:-230px}}
.m-cta{{width:1300px;height:1300px;left:-110px;top:-120px}}
.ghost{{position:absolute;right:70px;top:40px;font-family:'Anek Bangla';font-weight:800;
 font-size:420px;line-height:1;color:{p["red"]};opacity:.07;letter-spacing:-12px}}
.dark .ghost{{color:{p["gold_soft"]};opacity:.10}}
.content{{position:absolute;left:92px;right:92px;top:118px;bottom:170px;display:flex;
 flex-direction:column;gap:24px;z-index:2}}
.content.top{{bottom:auto;height:560px;justify-content:flex-start}}
.content.tight{{height:460px}}
.content.band-card{{height:400px;justify-content:center}}
.content.band-phone{{height:430px;justify-content:center}}
.content.festive{{top:190px}}
.center{{justify-content:center;text-align:center;align-items:center}}
.vcenter{{justify-content:center}}
.kicker{{align-self:flex-start;font-family:'Anek Bangla';font-weight:800;font-size:30px;
 letter-spacing:3px;text-transform:uppercase;color:{p["tint"]};background:{p["red"]};
 padding:10px 22px;border-radius:999px}}
.center .kicker{{align-self:center}}
.dark .kicker{{background:{p["gold"]};color:{p["red_deep"]}}}
.h-bn,.h-en{{font-family:'Anek Bangla',sans-serif;font-weight:800;color:{p["red_deep"]};
 letter-spacing:-1px}}
.h-bn{{font-size:92px;line-height:1.3}}
.h-en{{font-size:80px;line-height:1.12}}
.hook .h-bn{{font-size:112px}} .hook .h-en{{font-size:96px}}
.dark .h-bn,.dark .h-en{{color:#fff7ec;text-shadow:0 6px 30px rgba(60,0,0,.45)}}
mark{{background:none;color:inherit;position:relative;white-space:nowrap;z-index:0}}
mark::before{{content:"";position:absolute;left:-.12em;right:-.12em;bottom:.08em;height:.42em;
 background:{p["gold_soft"]};z-index:-1;transform:skew(-8deg);border-radius:6px}}
.dark mark{{color:{p["gold_soft"]}}}
.dark mark::before{{background:{p["gold"]};opacity:.55}}
.sub-en{{font-family:'Anek Bangla';font-weight:600;font-size:46px;line-height:1.22;
 color:{p["gold"]}}}
.dark .sub-en{{color:{p["gold_soft"]}}}
.b{{font-size:42px;line-height:1.45;font-weight:500;color:#3a2a28}}
.b-en{{font-size:36px;line-height:1.4;font-weight:500;color:#6a5652}}
.dark .b,.dark .b-en{{color:#fbe9d6}}
.fit{{overflow:visible}}
.sticker{{position:absolute;left:66px;bottom:250px;width:260px;height:260px;z-index:4;
 clip-path:{_starburst()};background:{p["gold"]};display:flex;align-items:center;
 justify-content:center;text-align:center;transform:rotate(-12deg);padding:44px}}
.sticker span{{font-family:'Anek Bangla';font-weight:800;font-size:38px;line-height:1.08;
 color:{p["red_deep"]}}}
.hero{{position:absolute;z-index:1;filter:drop-shadow(0 40px 50px rgba(40,0,0,.45))}}
.hero.card{{width:470px;right:76px;bottom:150px;transform:rotate(6deg);border-radius:10px}}
.hero.card2{{width:470px;left:60px;bottom:170px;transform:rotate(-8deg);opacity:.92}}
.stage{{position:absolute;left:0;right:0;bottom:0;height:520px;z-index:0;
 background:linear-gradient(180deg,{p["red"]} 0%,{p["red_deep"]} 100%)}}
.stage::before{{content:"";position:absolute;left:0;right:0;top:-2px;height:10px;
 background:{p["gold"]}}}
.dark .stage{{background:linear-gradient(180deg,{p["tint"]} 0%,{p["gold_soft"]} 100%)}}
.phone{{position:absolute;left:50%;bottom:140px;transform:translateX(-50%);height:650px;
 aspect-ratio:1080/1560;object-fit:cover;object-position:50% 100%;border-radius:36px;z-index:2;
 box-shadow:0 40px 80px rgba(30,0,0,.45),0 0 0 10px #fffaf4}}
.phone.small{{height:570px}}
.cardshot{{position:absolute;left:50%;bottom:150px;transform:translateX(-50%) rotate(-3deg);
 height:660px;border-radius:12px;z-index:2;box-shadow:0 40px 80px rgba(30,0,0,.45)}}
.swipe{{position:absolute;right:92px;bottom:128px;z-index:5;display:flex;align-items:center;
 gap:14px;font-family:'Anek Bangla';font-weight:800;font-size:34px;color:{p["red_deep"]};
 background:#fffaf4;padding:14px 26px;border-radius:999px;
 box-shadow:0 10px 30px rgba(40,0,0,.25)}}
.swipe b{{font-size:44px;line-height:1;color:{p["red"]}}}
.icon{{width:220px;height:220px;border-radius:50px;z-index:3;
 box-shadow:0 24px 60px rgba(0,0,0,.45),0 0 0 8px {p["gold_soft"]}55}}
.fan{{position:relative;width:640px;height:300px;margin-bottom:10px}}
.fan img.c{{position:absolute;top:30px;width:200px;border-radius:8px;
 box-shadow:0 20px 40px rgba(0,0,0,.4)}}
.fan img.c1{{left:30px;transform:rotate(-14deg)}}
.fan img.c2{{right:30px;transform:rotate(14deg)}}
.fan img.icon{{position:absolute;left:210px;top:30px}}
.pill{{margin-top:10px;display:inline-flex;align-items:center;gap:18px;padding:30px 54px;
 border-radius:999px;background:#fffaf4;color:{p["red_deep"]};font-family:'Anek Bangla';
 font-weight:800;font-size:48px;box-shadow:0 18px 40px rgba(30,0,0,.35)}}
.pill i{{font-style:normal;color:{p["share"]}}}
.foot{{position:absolute;left:92px;right:92px;bottom:64px;display:flex;
 justify-content:space-between;align-items:center;font-family:'Anek Bangla';font-weight:800;
 font-size:30px;color:{p["red_deep"]};z-index:5}}
.dark .foot{{color:{p["gold_soft"]}}}
.on-stage .foot{{color:{p["gold_soft"]}}}
.dark.on-stage .foot{{color:{p["red_deep"]}}}
.dots{{display:flex;gap:10px}}
.dots i{{width:14px;height:14px;border-radius:50%;background:currentColor;opacity:.3}}
.dots i.on{{opacity:1;width:34px;border-radius:8px}}
"""


# ----------------------------------------------------------------------------- text blocks


def _rich(text: str) -> str:
    """Escape model text, then turn *emphasis* into <mark>. Stray asterisks are dropped."""
    safe = escape(text)
    return EMPHASIS.sub(lambda m: f"<mark>{m.group(1)}</mark>", safe).replace("*", "")


def _texts(slide: Slide, language: Language, *, compact: bool, hook: bool = False) -> str:
    """Kicker + headlines always; bodies trimmed when space is tight (hook, or a visual below)."""
    out: list[str] = []
    if slide.kicker:
        out.append(f'<div class="kicker">{escape(slide.kicker.replace("*", ""))}</div>')
    if language is not Language.EN and slide.headline_bn:
        out.append(f'<h1 class="h-bn fit" data-fit>{_rich(slide.headline_bn)}</h1>')
    if slide.headline_en and language is not Language.BN:
        cls = "h-en" if language is Language.EN else "sub-en"
        out.append(f'<h2 class="{cls} fit" data-fit>{_rich(slide.headline_en)}</h2>')
    if hook:
        return "\n".join(out)
    show_bn_body = language is not Language.EN and slide.body_bn
    show_en_body = slide.body_en and language is not Language.BN
    if compact and language is Language.BN_EN:
        return "\n".join(out)  # bilingual with a visual: the two headlines carry it
    if show_bn_body:
        out.append(f'<p class="b fit" data-fit>{_rich(slide.body_bn or "")}</p>')
    if show_en_body:
        cls = "b" if language is Language.EN else "b-en"
        out.append(f'<p class="{cls} fit" data-fit>{_rich(slide.body_en or "")}</p>')
    return "\n".join(out)


def _footer(kit: BrandKit, ctx: SlideContext) -> str:
    dots = "".join(
        f'<i class="{"on" if i == ctx.index else ""}"></i>' for i in range(1, ctx.total + 1)
    )
    return (
        f'<div class="foot"><span>{escape(kit.name_bn)} · {escape(kit.name_en)}</span>'
        f'<span class="dots">{dots}</span></div>'
    )


def _swipe(language: Language) -> str:
    label = "সোয়াইপ করুন" if language is Language.BN else "Swipe"
    return f'<div class="swipe">{escape(label)} <b>›</b></div>'


def _num(n: int, language: Language) -> str:
    text = f"{n:02d}"
    return text.translate(BN_DIGITS) if language is Language.BN else text


def _theme(slide: Slide, ctx: SlideContext) -> str:
    if slide.role in (SlideRole.HOOK, SlideRole.CTA):
        return "dark"
    # Rhythm: alternate cream and warm gold; every third content slide goes dark for contrast.
    if ctx.content_number and ctx.content_number % 3 == 0 and not ctx.image_uri:
        return "dark"
    return "warm" if ctx.content_number % 2 == 0 else "cream"


# ----------------------------------------------------------------------------- layouts


def _hook(kit: BrandKit, slide: Slide, ctx: SlideContext) -> tuple[str, str]:
    festive = " festive" if ctx.festive else ""
    sticker = ""
    if slide.sticker:
        sticker = (
            f'<div class="sticker"><span>{escape(slide.sticker.replace("*", ""))}</span></div>'
        )
    garland = _garland() if ctx.festive else ""
    mandala = f'<div class="mandala m-hook">{_mandala(kit.palette["gold_soft"], 0.13)}</div>'
    texts = _texts(slide, ctx.language, compact=True, hook=True)
    if ctx.image_uri and ctx.image_kind == "CARD":
        body = (
            f'<div class="content top band-card{festive}">{texts}</div>'
            f'<img class="hero card" src="{escape(ctx.image_uri)}" alt="">'
        )
        return "dark", f"{mandala}{garland}{body}{sticker}{_swipe(ctx.language)}"
    if ctx.image_uri:
        body = (
            f'<div class="content top band-phone{festive}">{texts}</div><div class="stage"></div>'
            f'<img class="phone small" src="{escape(ctx.image_uri)}" alt="">'
        )
        return "dark on-stage", f"{mandala}{garland}{body}{sticker}{_swipe(ctx.language)}"
    body = f'<div class="content center{festive}">{texts}</div>'
    return "dark", f"{mandala}{garland}{body}{sticker}{_swipe(ctx.language)}"


def _content(kit: BrandKit, slide: Slide, ctx: SlideContext) -> tuple[str, str]:
    theme = _theme(slide, ctx)
    texts = _texts(slide, ctx.language, compact=bool(ctx.image_uri))
    if ctx.image_uri:
        visual = (
            f'<img class="cardshot" src="{escape(ctx.image_uri)}" alt="">'
            if ctx.image_kind == "CARD"
            else f'<img class="phone" src="{escape(ctx.image_uri)}" alt="">'
        )
        body = f'<div class="content top tight">{texts}</div><div class="stage"></div>{visual}'
        return f"{theme} on-stage", body
    numbered_kicker = bool(slide.kicker and re.search(r"[0-9০-৯]", slide.kicker))
    ghost = (
        f'<div class="ghost">{_num(ctx.content_number, ctx.language)}</div>'
        if ctx.content_number and not numbered_kicker
        else ""
    )
    color = kit.palette["gold_soft"] if theme == "dark" else kit.palette["gold"]
    corner = f'<div class="mandala m-corner">{_mandala(color, 0.35)}</div>'
    return theme, f'{ghost}{corner}<div class="content vcenter">{texts}</div>'


def _cta(kit: BrandKit, slide: Slide, ctx: SlideContext) -> tuple[str, str]:
    cards = ctx.card_uris[:2]
    icon = f'<img class="icon" src="{escape(ctx.icon_uri)}" alt="">' if ctx.icon_uri else ""
    if len(cards) == 2 and ctx.icon_uri:
        fan = (
            f'<div class="fan"><img class="c c1" src="{escape(cards[0])}" alt="">'
            f'<img class="c c2" src="{escape(cards[1])}" alt="">{icon}</div>'
        )
    else:
        fan = icon
    label = "Google Play-এ পাবেন" if ctx.language is Language.BN else "Get it on Google Play"
    mandala = f'<div class="mandala m-cta">{_mandala(kit.palette["gold_soft"], 0.12)}</div>'
    texts = _texts(slide, ctx.language, compact=True)
    body = (
        f'{mandala}<div class="content center">{fan}{texts}'
        f'<div class="pill"><i>▶</i> {escape(label)}</div></div>'
    )
    return "dark", body


def render_slide_html(kit: BrandKit, slide: Slide, ctx: SlideContext) -> str:
    if slide.role is SlideRole.HOOK:
        theme, body = _hook(kit, slide, ctx)
        theme += " hook"
    elif slide.role is SlideRole.CTA:
        theme, body = _cta(kit, slide, ctx)
    else:
        theme, body = _content(kit, slide, ctx)
    lang = "en" if ctx.language is Language.EN else "bn"
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
        f"<style>{_css(kit, ctx)}</style></head>"
        f'<body class="{theme}"><div class="frame"></div>'
        f"{body}{_footer(kit, ctx)}</body></html>"
    )
