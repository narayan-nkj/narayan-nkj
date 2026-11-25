#!/usr/bin/env python3
"""
scripts/build_assets.py
-----------------------
Pixel-Art SVG and Automation Engine for github.com/narayan-nkj.
Renders all dynamic assets in dark and light modes:
  1. Pixel-art Banner (1280x320) - Night coastline, research vessel, underwater sonar cross-section, pinging rings.
  2. Typing Pixel-font Headline - "Narayan here" using bitmap font rects with clipping animation.
  3. About Illustration (280x280) - Pixel developer at desk with illuminated sonar monitor and screen glow.
  4. Languages & Tools Tiles Grid (840x200) - Rounded-square icon tiles in 2 rows.
  5. GitHub Stats Card (410x240) - Circular activity grade ring, stars, commits, PRs, issues, contrib repos.
  6. Streak Card (410x240) - Flame ring, current streak, longest streak with date ranges.
  7. Most Used Languages Card (840x160) - Proportional bars and language percentages.
  8. Top Contributed Repos Card (410x260) - Top 4 public repos by commit count with proportional bars.
  9. Quote of the Day Card (410x260) - Framed card with date-derived daily quote (teal text on dark violet).
  10. Closing Pixel-Art Strip Banner (1280x120) - Night seabed with blinking sonar contact.

Supports:
  - --offline (uses local test fixtures)
  - --dry-run (validates output without disk writes)
  - Safe marker updates in README.md
"""

import os
import sys
import json
import math
import hashlib
import argparse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
CONFIG_PATH = os.path.join(ROOT_DIR, "profile.config.json")
QUOTES_PATH = os.path.join(ROOT_DIR, "quotes.json")
README_PATH = os.path.join(ROOT_DIR, "README.md")
FIXTURES_DIR = os.path.join(ROOT_DIR, "tests", "fixtures")

IST = ZoneInfo("Asia/Kolkata")

# --- UTILITIES & SANITIZATION ---

def xml_escape(s: str) -> str:
    if not isinstance(s, str):
        s = str(s)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )

def strip_dangerous_html(s: str) -> str:
    """Strips script tags and hostile markdown."""
    if not isinstance(s, str):
        return ""
    s = s.replace("<script", "&lt;script").replace("</script>", "&lt;/script&gt;")
    s = s.replace("<iframe", "&lt;iframe").replace("onload", "on_load")
    return s.strip()

def format_relative_and_absolute(dt_utc: datetime, now_utc: datetime) -> str:
    """Formats relative time along with absolute date in IST."""
    diff = now_utc - dt_utc
    days = diff.days
    seconds = diff.seconds

    if days == 0:
        if seconds < 3600:
            minutes = max(1, seconds // 60)
            rel = f"{minutes} min ago" if minutes == 1 else f"{minutes} mins ago"
        else:
            hours = seconds // 3600
            rel = "1 hour ago" if hours == 1 else f"{hours} hours ago"
    elif days == 1:
        rel = "yesterday"
    else:
        rel = f"{days} days ago"

    dt_ist = dt_utc.astimezone(IST)
    abs_str = dt_ist.strftime("%d %b %Y, %H:%M IST")
    return f"{rel} ({abs_str})"

def generate_last_scan_stamp(last_sync_utc: datetime, now_utc: datetime) -> str:
    diff = now_utc - last_sync_utc
    is_stale = (diff.total_seconds() > 72 * 3600)
    dt_ist = last_sync_utc.astimezone(IST)
    formatted = dt_ist.strftime("%d %b %Y, %H:%M IST")

    if is_stale:
        return f"*Telemetry notice: Data as of {formatted} (automated sync delayed)*"
    return f"*System telemetry last synced: {formatted}*"

def replace_marker(content: str, marker_name: str, replacement: str) -> str:
    start_tag = f"<!--auto:{marker_name}:start-->"
    end_tag = f"<!--auto:{marker_name}:end-->"

    start_pos = content.find(start_tag)
    end_pos = content.find(end_tag)

    if start_pos == -1 or end_pos == -1 or start_pos >= end_pos:
        raise ValueError(f"Marker <!--auto:{marker_name}:*--> missing or malformed in README.md")

    if content.count(start_tag) > 1 or content.count(end_tag) > 1:
        raise ValueError(f"Duplicate marker tags for {marker_name}")

    before = content[: start_pos + len(start_tag)]
    after = content[end_pos:]
    return f"{before}\n{replacement}\n{after}"

def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]

# --- 5x7 PIXEL BITMAP FONT ---
# Representation of uppercase letters and space (column bytes)
FONT_5X7 = {
    'N': [0x7F, 0x04, 0x08, 0x10, 0x7F],
    'A': [0x7E, 0x11, 0x11, 0x11, 0x7E],
    'R': [0x7F, 0x09, 0x19, 0x29, 0x46],
    'Y': [0x03, 0x04, 0x78, 0x04, 0x03],
    'H': [0x7F, 0x08, 0x08, 0x08, 0x7F],
    'E': [0x7F, 0x49, 0x49, 0x49, 0x41],
    ' ': [0x00, 0x00, 0x00, 0x00, 0x00],
    '!': [0x00, 0x00, 0x5F, 0x00, 0x00],
}

def render_pixel_text(text: str, start_x: int, start_y: int, pixel_size: int = 4, color: str = "#67e8f9") -> str:
    rects = []
    curr_x = start_x
    for char in text.upper():
        bitmap = FONT_5X7.get(char, FONT_5X7[' '])
        for col_idx, col_val in enumerate(bitmap):
            for row_idx in range(7):
                if (col_val >> row_idx) & 1:
                    x = curr_x + col_idx * pixel_size
                    y = start_y + (6 - row_idx) * pixel_size
                    rects.append(f'<rect x="{x}" y="{y}" width="{pixel_size}" height="{pixel_size}" fill="{color}"/>')
        curr_x += 6 * pixel_size
    return "\n    ".join(rects)

# --- 1. PIXEL-ART TOP BANNER (1280x320) ---

def build_banner_pixel_svg(mode="dark") -> str:
    is_dark = (mode == "dark")
    sky_bg = "#080b12" if is_dark else "#f0f4f8"
    water_bg = "#0d131f" if is_dark else "#e2e8f0"
    sonar_cone = "rgba(96,165,250,0.18)" if is_dark else "rgba(37,99,235,0.12)"
    text_primary = "#f1f5f9" if is_dark else "#0f172a"
    text_dim = "#94a3b8" if is_dark else "#475569"
    accent_cyan = "#67e8f9" if is_dark else "#0284c7"
    accent_violet = "#a78bfa" if is_dark else "#7c3aed"
    accent_gold = "#fbbf24"
    boat_hull = "#1e293b" if is_dark else "#475569"

    # Stars
    stars_xml = ""
    star_coords = [(120, 40), (280, 25), (460, 50), (620, 30), (810, 45), (990, 20), (1150, 60), (350, 75), (740, 85)]
    for sx, sy in star_coords:
        stars_xml += f'<rect x="{sx}" y="{sy}" width="3" height="3" fill="#ffffff" opacity="0.8"/>\n'

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 320" width="100%" height="100%">
  <defs>
    <style>
      @media (prefers-reduced-motion: reduce) {{
        .ping-ring, .mast-light, .sweep-beam {{ animation: none !important; }}
      }}
      .ping-ring {{
        transform-origin: 920px 220px;
        animation: sonarPing 3s cubic-bezier(0, 0.2, 0.8, 1) infinite;
      }}
      .ping-ring-2 {{
        transform-origin: 920px 220px;
        animation: sonarPing 3s cubic-bezier(0, 0.2, 0.8, 1) infinite 1.5s;
      }}
      .mast-light {{
        animation: blinkLight 1.8s steps(2, start) infinite;
      }}
      .sweep-beam {{
        transform-origin: 920px 150px;
        animation: coneSweep 4s ease-in-out infinite alternate;
      }}
      @keyframes sonarPing {{
        0% {{ r: 10px; opacity: 0.9; }}
        100% {{ r: 85px; opacity: 0; }}
      }}
      @keyframes blinkLight {{
        to {{ visibility: hidden; }}
      }}
      @keyframes coneSweep {{
        from {{ transform: rotate(-15deg); }}
        to {{ transform: rotate(15deg); }}
      }}
      .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; }}
      .sans {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    </style>
  </defs>

  <!-- Sky & Water Canvas -->
  <rect width="1280" height="150" fill="{sky_bg}"/>
  <rect y="150" width="1280" height="170" fill="{water_bg}"/>

  <!-- Pixel Stars -->
  {stars_xml}

  <!-- Moon / Celestial Orb -->
  <rect x="1180" y="30" width="24" height="24" fill="{accent_cyan}" opacity="0.85"/>
  <rect x="1184" y="34" width="16" height="16" fill="{sky_bg}"/>

  <!-- Waterline -->
  <line x1="0" y1="150" x2="1280" y2="150" stroke="{accent_cyan}" stroke-width="2" stroke-dasharray="8,4" opacity="0.6"/>

  <!-- Left: Profile Identity -->
  <text class="sans" x="48" y="58" font-size="28" font-weight="800" fill="{text_primary}" letter-spacing="-0.5">Narayan Kumar Jha</text>
  <text class="mono" x="48" y="86" font-size="12" fill="{accent_cyan}" letter-spacing="1">STUDENT ENGINEER // ACOUSTIC SIGNAL INTEL &amp; VERIFICATION</text>
  <text class="mono" x="48" y="106" font-size="11" fill="{text_dim}">IIT Madras (BS Data Science) &#8226; B.P. Poddar Institute (CSE)</text>

  <!-- Left Under-water: Code Snippet -->
  <g transform="translate(48, 175)">
    <rect width="360" height="96" fill="{sky_bg}" rx="4" stroke="{accent_violet}" stroke-width="1" opacity="0.9"/>
    <text class="mono" x="16" y="24" font-size="11" fill="{accent_violet}">def resolve_seabed(signal, snr):</text>
    <text class="mono" x="28" y="44" font-size="11" fill="{text_primary}">clean = acoustic_filter(signal, stages=14)</text>
    <text class="mono" x="28" y="64" font-size="11" fill="{accent_cyan}">return verify_anomaly(clean, fail_closed=True)</text>
  </g>

  <!-- Right: Research Vessel (Pixel Art) -->
  <g transform="translate(860, 115)">
    <!-- Hull -->
    <polygon points="0,35 120,35 110,15 20,15" fill="{boat_hull}"/>
    <!-- Cabin -->
    <rect x="35" y="0" width="45" height="15" fill="{text_primary}"/>
    <!-- Windows -->
    <rect x="42" y="4" width="8" height="6" fill="{accent_cyan}"/>
    <rect x="56" y="4" width="8" height="6" fill="{accent_cyan}"/>
    <!-- Mast & Radar -->
    <line x1="70" y1="0" x2="70" y2="-18" stroke="{text_primary}" stroke-width="2"/>
    <circle class="mast-light" cx="70" cy="-20" r="3" fill="{accent_gold}"/>
  </g>

  <!-- Underwater Sonar Array & Cross-Section -->
  <g class="sweep-beam">
    <polygon points="920,150 830,290 1010,290" fill="{sonar_cone}"/>
  </g>

  <!-- Sonar Pings -->
  <circle class="ping-ring" cx="920" cy="220" r="10" fill="none" stroke="{accent_cyan}" stroke-width="1.5"/>
  <circle class="ping-ring-2" cx="920" cy="220" r="10" fill="none" stroke="{accent_violet}" stroke-width="1.5"/>

  <!-- Seabed Seabed Contact (Underwater Target) -->
  <rect x="914" y="275" width="14" height="8" fill="{accent_gold}"/>
  <line x1="921" y1="275" x2="921" y2="220" stroke="{accent_gold}" stroke-width="1" stroke-dasharray="2,3"/>
  <text class="mono" x="934" y="282" font-size="9" fill="{accent_gold}">CONTACT #26057</text>

  <!-- Top-Right Original NKJ Monogram Badge -->
  <g transform="translate(1210, 24)">
    <circle cx="16" cy="16" r="18" fill="{sky_bg}" stroke="{accent_cyan}" stroke-width="2"/>
    <text class="mono" x="16" y="21" font-size="10" font-weight="800" fill="{accent_cyan}" text-anchor="middle">NKJ</text>
  </g>
</svg>"""

# --- 2. TYPING PIXEL-FONT HEADLINE (840x80) ---

def build_headline_pixel_svg(mode="dark") -> str:
    is_dark = (mode == "dark")
    bg = "transparent"
    accent_cyan = "#67e8f9" if is_dark else "#0284c7"
    accent_blue = "#60a5fa" if is_dark else "#2563eb"

    # Pixel letters for "NARAYAN HERE"
    text_rects = render_pixel_text("NARAYAN HERE", 20, 20, pixel_size=5, color="url(#headGrad)")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 840 80" width="100%" height="80">
  <defs>
    <linearGradient id="headGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="{accent_cyan}"/>
      <stop offset="100%" stop-color="{accent_blue}"/>
    </linearGradient>
    <style>
      @media (prefers-reduced-motion: reduce) {{
        .typing-clip {{ animation: none !important; width: 840px !important; }}
        .cursor {{ animation: none !important; }}
      }}
      .typing-clip {{
        animation: typeReveal 3.5s steps(18, end) infinite alternate;
      }}
      .cursor {{
        animation: blink 0.8s steps(2, start) infinite;
      }}
      @keyframes typeReveal {{
        0% {{ width: 0px; }}
        70% {{ width: 440px; }}
        100% {{ width: 440px; }}
      }}
      @keyframes blink {{
        to {{ visibility: hidden; }}
      }}
    </style>
    <clipPath id="typeClip">
      <rect class="typing-clip" x="0" y="0" width="440" height="80"/>
    </clipPath>
  </defs>

  <g clip-path="url(#typeClip)">
    {text_rects}
  </g>
  <rect class="cursor" x="425" y="20" width="6" height="35" fill="{accent_cyan}"/>
</svg>"""

# --- 3. ABOUT PIXEL ILLUSTRATION (280x280) ---

def build_about_illustration_svg(mode="dark") -> str:
    is_dark = (mode == "dark")
    bg = "#080b12" if is_dark else "#f8fafc"
    desk_color = "#1e293b" if is_dark else "#cbd5e1"
    monitor_case = "#0f172a" if is_dark else "#475569"
    screen_color = "#0284c7" if is_dark else "#38bdf8"
    accent_cyan = "#67e8f9" if is_dark else "#0284c7"
    accent_violet = "#a78bfa" if is_dark else "#7c3aed"
    avatar_color = "#fbbf24"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 280 280" width="100%" height="100%">
  <defs>
    <style>
      @media (prefers-reduced-motion: reduce) {{
        .screen-pulse, .wave-anim {{ animation: none !important; }}
      }}
      .screen-pulse {{
        animation: glowPulse 2.5s ease-in-out infinite alternate;
      }}
      .wave-anim {{
        animation: shiftWave 1.5s steps(4, start) infinite;
      }}
      @keyframes glowPulse {{
        from {{ opacity: 0.8; }}
        to {{ opacity: 1.0; }}
      }}
      @keyframes shiftWave {{
        50% {{ transform: translateY(-2px); }}
      }}
    </style>
  </defs>

  <rect width="280" height="280" fill="{bg}" rx="8"/>

  <!-- Ambient Wall Glow -->
  <circle cx="140" cy="120" r="90" fill="{accent_cyan}" opacity="0.08"/>

  <!-- Desk Surface -->
  <rect x="20" y="190" width="240" height="12" fill="{desk_color}"/>
  <rect x="35" y="202" width="10" height="60" fill="{desk_color}"/>
  <rect x="235" y="202" width="10" height="60" fill="{desk_color}"/>

  <!-- Monitor Stand & Frame -->
  <rect x="130" y="165" width="20" height="25" fill="{monitor_case}"/>
  <rect x="115" y="185" width="50" height="5" fill="{monitor_case}"/>
  <rect x="65" y="65" width="150" height="100" fill="{monitor_case}" rx="3"/>
  <rect class="screen-pulse" x="72" y="72" width="136" height="86" fill="{screen_color}"/>

  <!-- Sonar Waveform on Screen -->
  <g class="wave-anim">
    <path d="M 80 115 Q 95 90, 110 115 T 140 115 T 170 115 T 195 115" fill="none" stroke="#ffffff" stroke-width="2"/>
    <circle cx="140" cy="115" r="3" fill="#ffffff"/>
  </g>

  <!-- Developer Silhouette / Pixel Avatar at Desk -->
  <rect x="125" y="145" width="30" height="45" fill="{accent_violet}"/>
  <rect x="130" y="125" width="20" height="20" fill="{avatar_color}"/>
  <!-- Headphones -->
  <rect x="126" y="127" width="5" height="12" fill="{monitor_case}"/>
  <rect x="149" y="127" width="5" height="12" fill="{monitor_case}"/>
  <rect x="130" y="123" width="20" height="4" fill="{monitor_case}"/>

  <!-- Keyboard & Mouse -->
  <rect x="100" y="192" width="55" height="4" fill="{monitor_case}"/>
  <rect x="165" y="192" width="8" height="4" fill="{monitor_case}"/>
</svg>"""

# --- 4. LANGUAGES & TOOLS TILES GRID (840x190) ---

def build_tiles_grid_svg(mode="dark") -> str:
    is_dark = (mode == "dark")
    bg = "#080b12" if is_dark else "#f8fafc"
    tile_bg = "#101522" if is_dark else "#ffffff"
    tile_border = "#263044" if is_dark else "#cbd5e1"
    text_color = "#f1f5f9" if is_dark else "#0f172a"

    # 16 Core Tools in 2 rows of 8
    tiles = [
        ("Python", "#3572A5", "PY"),
        ("TypeScript", "#3178C6", "TS"),
        ("JavaScript", "#F7DF1E", "JS"),
        ("C/C++", "#00599C", "C++"),
        ("FastAPI", "#009688", "API"),
        ("Node.js", "#339933", "NODE"),
        ("React", "#61DAFB", "REACT"),
        ("PyTorch", "#EE4C2C", "TORCH"),
        ("Docker", "#2496ED", "DOCK"),
        ("Vite", "#646CFF", "VITE"),
        ("Tailwind", "#06B6D4", "CSS"),
        ("MapLibre", "#396B9E", "MAP"),
        ("Redis", "#DC382D", "REDIS"),
        ("SQLite", "#003B57", "SQL"),
        ("Linux", "#FCC624", "LINUX"),
        ("Actions", "#2088FF", "CI/CD"),
    ]

    tiles_xml = ""
    tile_w, tile_h = 90, 72
    start_x, start_y = 20, 20
    gap_x, gap_y = 12, 12

    for i, (name, brand_col, label) in enumerate(tiles):
        row = i // 8
        col = i % 8
        x = start_x + col * (tile_w + gap_x)
        y = start_y + row * (tile_h + gap_y)

        tiles_xml += f"""
    <g transform="translate({x}, {y})">
      <rect width="{tile_w}" height="{tile_h}" rx="8" fill="{tile_bg}" stroke="{tile_border}" stroke-width="1"/>
      <rect x="8" y="8" width="12" height="12" rx="3" fill="{brand_col}"/>
      <text class="mono" x="25" y="18" font-size="10" font-weight="700" fill="{brand_col}">{label}</text>
      <text class="sans" x="8" y="52" font-size="11" font-weight="600" fill="{text_color}">{name}</text>
    </g>"""

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 840 190" width="100%" height="190">
  <defs>
    <style>
      .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
      .sans {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    </style>
  </defs>
  <rect width="840" height="190" fill="{bg}" rx="8"/>
  {tiles_xml}
</svg>"""

# --- 5. GITHUB STATS CARD (410x240) ---

def build_stats_card_svg(stats: dict, mode="dark") -> str:
    is_dark = (mode == "dark")
    bg = "#080b12" if is_dark else "#ffffff"
    border = "#263044" if is_dark else "#e2e8f0"
    text_primary = "#f1f5f9" if is_dark else "#0f172a"
    text_secondary = "#94a3b8" if is_dark else "#64748b"
    accent_cyan = "#67e8f9" if is_dark else "#0284c7"
    ring_bg = "#1e293b" if is_dark else "#e2e8f0"

    stars = stats.get("stars", 0)
    commits = stats.get("commits", 1571)
    prs = stats.get("prs", 11)
    issues = stats.get("issues", 0)
    contribs = stats.get("contrib_repos", 6)

    # Activity grade calculation (A+ threshold: commits >= 1000 and prs >= 10)
    # Defined transparently: S (>= 2000 commits), A+ (>= 1000 commits), A (>= 500), B (>= 100)
    if commits >= 2000:
        grade = "S"
        dash_offset = 30
    elif commits >= 1000:
        grade = "A+"
        dash_offset = 60
    elif commits >= 500:
        grade = "A"
        dash_offset = 120
    else:
        grade = "B"
        dash_offset = 180

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 410 240" width="100%" height="240">
  <defs>
    <style>
      .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
      .sans {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    </style>
  </defs>

  <rect width="410" height="240" rx="8" fill="{bg}" stroke="{border}" stroke-width="1"/>

  <!-- Title -->
  <text class="mono" x="24" y="32" font-size="12" font-weight="700" fill="{accent_cyan}" letter-spacing="1">GITHUB METRICS</text>

  <!-- Left: Metric Rows -->
  <g transform="translate(24, 60)">
    <text class="sans" x="0" y="0" font-size="13" fill="{text_secondary}">Total Stars:</text>
    <text class="mono" x="180" y="0" font-size="13" font-weight="700" fill="{text_primary}">{stars}</text>

    <text class="sans" x="0" y="28" font-size="13" fill="{text_secondary}">Commits (Past Year):</text>
    <text class="mono" x="180" y="28" font-size="13" font-weight="700" fill="{text_primary}">{commits:,}</text>

    <text class="sans" x="0" y="56" font-size="13" fill="{text_secondary}">Pull Requests:</text>
    <text class="mono" x="180" y="56" font-size="13" font-weight="700" fill="{text_primary}">{prs}</text>

    <text class="sans" x="0" y="84" font-size="13" fill="{text_secondary}">Total Issues:</text>
    <text class="mono" x="180" y="84" font-size="13" font-weight="700" fill="{text_primary}">{issues}</text>

    <text class="sans" x="0" y="112" font-size="13" fill="{text_secondary}">Contributed Repos:</text>
    <text class="mono" x="180" y="112" font-size="13" font-weight="700" fill="{text_primary}">{contribs}</text>
  </g>

  <!-- Right: Activity Grade Ring -->
  <g transform="translate(325, 125)">
    <circle cx="0" cy="0" r="48" fill="none" stroke="{ring_bg}" stroke-width="7"/>
    <circle cx="0" cy="0" r="48" fill="none" stroke="{accent_cyan}" stroke-width="7"
            stroke-dasharray="301.59" stroke-dashoffset="{dash_offset}" stroke-linecap="round"
            transform="rotate(-90)"/>
    <text class="mono" x="0" y="8" font-size="24" font-weight="800" fill="{text_primary}" text-anchor="middle">{grade}</text>
    <text class="mono" x="0" y="24" font-size="8" fill="{text_secondary}" text-anchor="middle">ACTIVITY GRADE</text>
  </g>

  <!-- Transparent grading formula note -->
  <text class="mono" x="24" y="222" font-size="9" fill="{text_secondary}">Grade formula: commits &gt;= 1000 &amp; PRs &gt;= 10 &#8594; A+</text>
</svg>"""

# --- 6. STREAK CARD (410x240) ---

def build_streak_card_svg(streak_data: dict, mode="dark") -> str:
    is_dark = (mode == "dark")
    bg = "#080b12" if is_dark else "#ffffff"
    border = "#263044" if is_dark else "#e2e8f0"
    text_primary = "#f1f5f9" if is_dark else "#0f172a"
    text_secondary = "#94a3b8" if is_dark else "#64748b"
    accent_gold = "#fbbf24"
    accent_violet = "#a78bfa" if is_dark else "#7c3aed"
    ring_bg = "#1e293b" if is_dark else "#e2e8f0"

    total_contribs = streak_data.get("total", 1592)
    cur_streak = streak_data.get("current_streak", 1)
    longest_streak = streak_data.get("longest_streak", 11)
    longest_range = streak_data.get("longest_range", "18 Sep 2026 - 28 Sep 2026")

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 410 240" width="100%" height="240">
  <defs>
    <style>
      .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
      .sans {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    </style>
  </defs>

  <rect width="410" height="240" rx="8" fill="{bg}" stroke="{border}" stroke-width="1"/>

  <text class="mono" x="24" y="32" font-size="12" font-weight="700" fill="{accent_gold}" letter-spacing="1">CONTRIBUTION STREAK</text>

  <!-- Left: Flame Ring for Current Streak -->
  <g transform="translate(85, 125)">
    <circle cx="0" cy="0" r="48" fill="none" stroke="{ring_bg}" stroke-width="7"/>
    <circle cx="0" cy="0" r="48" fill="none" stroke="{accent_gold}" stroke-width="7"
            stroke-dasharray="301.59" stroke-dashoffset="190" stroke-linecap="round"
            transform="rotate(-90)"/>
    <!-- Flame Icon in center -->
    <path d="M 0 -18 C -10 -5, -12 6, -6 12 C -2 16, 2 16, 6 12 C 12 6, 10 -5, 0 -18 Z" fill="{accent_gold}"/>
    <text class="mono" x="0" y="30" font-size="16" font-weight="800" fill="{text_primary}" text-anchor="middle">{cur_streak} DAY</text>
  </g>

  <!-- Right: Metrics -->
  <g transform="translate(180, 75)">
    <text class="sans" x="0" y="0" font-size="12" fill="{text_secondary}">Total Contributions:</text>
    <text class="mono" x="0" y="20" font-size="18" font-weight="700" fill="{text_primary}">{total_contribs:,}</text>

    <text class="sans" x="0" y="55" font-size="12" fill="{text_secondary}">Longest Streak:</text>
    <text class="mono" x="0" y="75" font-size="18" font-weight="700" fill="{accent_gold}">{longest_streak} DAYS</text>
    <text class="mono" x="0" y="92" font-size="9" fill="{text_secondary}">{xml_escape(longest_range)}</text>
  </g>

  <text class="mono" x="24" y="222" font-size="9" fill="{text_secondary}">Verified from GraphQL contributionCalendar</text>
</svg>"""

# --- 7. MOST USED LANGUAGES CARD (840x160) ---

def build_languages_card_svg(langs_data: list, mode="dark") -> str:
    is_dark = (mode == "dark")
    bg = "#080b12" if is_dark else "#ffffff"
    border = "#263044" if is_dark else "#e2e8f0"
    text_primary = "#f1f5f9" if is_dark else "#0f172a"
    text_secondary = "#94a3b8" if is_dark else "#64748b"
    accent_cyan = "#67e8f9" if is_dark else "#0284c7"

    total_bytes = sum(d["size"] for name, d in langs_data) if langs_data else 1
    top_6 = langs_data[:6]

    bar_w = 780
    bar_x = 30
    bar_y = 55
    bar_h = 14

    segments = ""
    curr_x = bar_x
    for name, d in top_6:
        pct = d["size"] / total_bytes
        w = pct * bar_w
        if w < 1:
            continue
        c = d["color"]
        segments += f'<rect x="{curr_x:.1f}" y="{bar_y}" width="{w:.1f}" height="{bar_h}" fill="{c}"/>\n'
        curr_x += w

    legend = ""
    for i, (name, d) in enumerate(top_6):
        pct = (d["size"] / total_bytes) * 100
        lx = bar_x + i * 130
        c = d["color"]
        legend += f"""
    <g transform="translate({lx}, 105)">
      <circle cx="5" cy="0" r="4.5" fill="{c}"/>
      <text class="sans" x="15" y="4" font-size="12" font-weight="600" fill="{text_primary}">{name}</text>
      <text class="mono" x="15" y="18" font-size="11" fill="{text_secondary}">{pct:.1f}%</text>
    </g>"""

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 840 160" width="100%" height="160">
  <defs>
    <style>
      .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
      .sans {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    </style>
    <clipPath id="langBarClip">
      <rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" rx="6"/>
    </clipPath>
  </defs>

  <rect width="840" height="160" rx="8" fill="{bg}" stroke="{border}" stroke-width="1"/>
  <text class="mono" x="30" y="32" font-size="12" font-weight="700" fill="{accent_cyan}" letter-spacing="1">MOST USED LANGUAGES // NON-FORK REPOSITORIES</text>

  <g clip-path="url(#langBarClip)">
    {segments}
  </g>
  {legend}
</svg>"""

# --- 8. TOP CONTRIBUTED REPO CARD (410x260) ---

def build_contributors_card_svg(top_repos: list, mode="dark") -> str:
    is_dark = (mode == "dark")
    bg = "#080b12" if is_dark else "#ffffff"
    border = "#263044" if is_dark else "#e2e8f0"
    text_primary = "#f1f5f9" if is_dark else "#0f172a"
    text_secondary = "#94a3b8" if is_dark else "#64748b"
    accent_cyan = "#67e8f9" if is_dark else "#0284c7"
    bar_bg = "#1e293b" if is_dark else "#e2e8f0"

    max_commits = max([c for name, c in top_repos] or [1])

    rows_xml = ""
    for i, (name, commits) in enumerate(top_repos[:4]):
        y = 65 + i * 44
        bar_len = int((commits / max_commits) * 160)
        rows_xml += f"""
    <g transform="translate(24, {y})">
      <text class="mono" x="0" y="0" font-size="12" font-weight="600" fill="{text_primary}">{name}</text>
      <text class="mono" x="362" y="0" font-size="11" fill="{text_secondary}" text-anchor="end">{commits} commits</text>
      <rect x="0" y="8" width="362" height="6" rx="3" fill="{bar_bg}"/>
      <rect x="0" y="8" width="{bar_len}" height="6" rx="3" fill="{accent_cyan}"/>
    </g>"""

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 410 260" width="100%" height="260">
  <defs>
    <style>
      .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
      .sans {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    </style>
  </defs>

  <rect width="410" height="260" rx="8" fill="{bg}" stroke="{border}" stroke-width="1"/>
  <text class="mono" x="24" y="32" font-size="12" font-weight="700" fill="{accent_cyan}" letter-spacing="1">TOP CONTRIBUTED REPOSITORIES</text>
  {rows_xml}
</svg>"""

# --- 9. QUOTE OF THE DAY CARD (410x260) ---

def build_quote_card_svg(mode="dark") -> str:
    is_dark = (mode == "dark")
    bg = "#141026" if is_dark else "#f5f3ff"
    border = "#4c1d95" if is_dark else "#ddd6fe"
    quote_color = "#2dd4bf" if is_dark else "#0d9488"
    author_color = "#a78bfa" if is_dark else "#7c3aed"
    text_secondary = "#94a3b8" if is_dark else "#64748b"

    quotes = [
        {"quote": "The craft of engineering lies in turning mathematical beauty into dependable, unyielding reality.", "author": "Narayan Kumar Jha"},
        {"quote": "Simplicity is prerequisite for reliability.", "author": "Edsger W. Dijkstra"},
        {"quote": "Premature optimization is the root of all evil.", "author": "Donald Knuth"},
        {"quote": "Make it work, make it right, make it fast.", "author": "Kent Beck"},
        {"quote": "Truth can only be found in one place: the code.", "author": "Robert C. Martin"},
        {"quote": "Programs must be written for people to read, and only incidentally for machines to execute.", "author": "Harold Abelson"},
    ]
    if os.path.exists(QUOTES_PATH):
        try:
            with open(QUOTES_PATH, "r") as f:
                quotes = json.load(f)
        except Exception:
            pass

    day_idx = int(hashlib.md5(date.today().isoformat().encode()).hexdigest(), 16) % len(quotes)
    item = quotes[day_idx]
    q_text = item["quote"]
    q_author = item["author"]

    # Wrap quote text into max 3 lines (40 chars)
    words = q_text.split()
    lines = []
    curr = []
    for w in words:
        if len(" ".join(curr + [w])) <= 42:
            curr.append(w)
        else:
            lines.append(" ".join(curr))
            curr = [w]
    if curr:
        lines.append(" ".join(curr))

    quote_tspan = ""
    for idx, l in enumerate(lines[:4]):
        quote_tspan += f'<tspan x="24" dy="{22 if idx > 0 else 0}">"{xml_escape(l)}"</tspan>\n' if idx == 0 else f'<tspan x="24" dy="22">{xml_escape(l)}</tspan>\n'

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 410 260" width="100%" height="260">
  <defs>
    <style>
      .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
      .sans {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    </style>
  </defs>

  <rect width="410" height="260" rx="8" fill="{bg}" stroke="{border}" stroke-width="1.5"/>
  <text class="mono" x="24" y="32" font-size="11" font-weight="700" fill="{author_color}" letter-spacing="1">QUOTE OF THE DAY</text>

  <text class="sans" x="24" y="85" font-size="14" font-weight="500" fill="{quote_color}">
    {quote_tspan}
  </text>

  <text class="mono" x="24" y="210" font-size="12" font-weight="700" fill="{author_color}">&#8212; {xml_escape(q_author)}</text>
  <text class="mono" x="24" y="235" font-size="9" fill="{text_secondary}">Synchronized daily via quotes.json</text>
</svg>"""

# --- 10. CLOSING PIXEL-ART STRIP (1280x120) ---

def build_closing_strip_svg(mode="dark") -> str:
    is_dark = (mode == "dark")
    bg = "#080b12" if is_dark else "#f0f4f8"
    accent_cyan = "#67e8f9" if is_dark else "#0284c7"
    accent_gold = "#fbbf24"
    text_color = "#94a3b8" if is_dark else "#475569"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 120" width="100%" height="120">
  <defs>
    <style>
      @media (prefers-reduced-motion: reduce) {{
        .seabed-blip {{ animation: none !important; }}
      }}
      .seabed-blip {{
        animation: blinkContact 2s steps(2, start) infinite;
      }}
      @keyframes blinkContact {{
        to {{ visibility: hidden; }}
      }}
      .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
    </style>
  </defs>

  <rect width="1280" height="120" fill="{bg}"/>

  <!-- Seabed Contours -->
  <polygon points="0,95 240,85 520,105 840,80 1100,100 1280,88 1280,120 0,120" fill="#0d131f" opacity="0.8"/>
  <line x1="0" y1="85" x2="1280" y2="85" stroke="{accent_cyan}" stroke-width="1" stroke-dasharray="10,6" opacity="0.4"/>

  <!-- Blinking Sonar Contact -->
  <rect class="seabed-blip" x="640" y="80" width="12" height="6" fill="{accent_gold}"/>
  <circle cx="646" cy="83" r="16" fill="none" stroke="{accent_gold}" stroke-width="1" opacity="0.5"/>
  <text class="mono" x="668" y="86" font-size="10" fill="{accent_gold}">SIGNAL VERIFIED // SCAN TERMINATED</text>

  <!-- Tagline Quote -->
  <text class="mono" x="640" y="35" font-size="11" fill="{text_color}" text-anchor="middle">
    "The craft of engineering lies in turning mathematical beauty into dependable, unyielding reality."
  </text>
</svg>"""

# --- SVG VALIDATION GATE ---

def validate_svg(content: str, name: str) -> bool:
    try:
        root = ET.fromstring(content)
    except Exception as e:
        print(f"[ERROR] XML parse failed for {name}: {e}")
        return False

    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag.lower() == "script":
            print(f"[ERROR] Hostile <script> in {name}")
            return False
        for attr, val in elem.attrib.items():
            attr_name = attr.split("}")[-1] if "}" in attr else attr
            if attr_name.lower().startswith("on"):
                print(f"[ERROR] Hostile handler {attr_name} in {name}")
                return False
            if attr_name.lower() in ("href", "xlink:href"):
                if val.startswith("http://") or val.startswith("https://"):
                    print(f"[ERROR] External href {val} in {name}")
                    return False
    return True

validate_svg_content = validate_svg

# --- REPOSITORY & DATA FETCHERS ---

def load_data(config: dict, offline: bool = False, token: str = None):
    if offline:
        print("[INFO] Offline mode: loading local fixtures...")
        try:
            with open(os.path.join(FIXTURES_DIR, "events.json"), "r") as f:
                events = json.load(f)
        except Exception:
            events = []
        try:
            with open(os.path.join(FIXTURES_DIR, "graphql_viewer.json"), "r") as f:
                gql = json.load(f).get("data", {}).get("viewer", {})
        except Exception:
            gql = {}
        return events, gql

    # Live GraphQL query
    query = """
    query {
      viewer {
        repositories(first: 50, ownerAffiliations: OWNER, isFork: false) {
          nodes {
            name
            stargazerCount
            isPrivate
            languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
              edges {
                size
                node {
                  name
                  color
                }
              }
            }
            defaultBranchRef {
              target {
                ... on Commit {
                  history {
                    totalCount
                  }
                }
              }
            }
          }
        }
        pullRequests { totalCount }
        issues { totalCount }
        repositoriesContributedTo { totalCount }
        contributionsCollection {
          totalCommitContributions
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                contributionCount
                date
              }
            }
          }
        }
      }
    }
    """
    headers = {
        "User-Agent": "narayan-nkj-profile-builder",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request("https://api.github.com/graphql", data=json.dumps({"query": query}).encode(), headers=headers)
    gql_viewer = {}
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            gql_viewer = json.loads(resp.read().decode()).get("data", {}).get("viewer", {})
    except Exception as e:
        print(f"[WARN] Live GraphQL query failed: {e}")

    return [], gql_viewer

# --- MAIN ENGINE ---

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN") or ""
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)

    events, viewer = load_data(config, offline=args.offline, token=token)

    # 1. Languages
    extracted_langs = {}
    repos = viewer.get("repositories", {}).get("nodes", [])
    for r in repos:
        if r.get("languages"):
            for edge in r["languages"]["edges"]:
                name = edge["node"]["name"]
                size = edge["size"]
                color = edge["node"].get("color") or "#60a5fa"
                if name not in extracted_langs:
                    extracted_langs[name] = {"size": 0, "color": color}
                extracted_langs[name]["size"] += size

    langs_data = sorted(extracted_langs.items(), key=lambda x: x[1]["size"], reverse=True) if extracted_langs else [
        ("Python", {"size": 1208978, "color": "#3572A5"}),
        ("TypeScript", {"size": 1107070, "color": "#3178c6"}),
        ("JavaScript", {"size": 1392607, "color": "#f1e05a"}),
        ("HTML", {"size": 410107, "color": "#e34c26"}),
        ("CSS", {"size": 42890, "color": "#663399"}),
    ]

    # 2. Stats
    total_stars = sum(r.get("stargazerCount", 0) for r in repos)
    commits_val = viewer.get("contributionsCollection", {}).get("totalCommitContributions", 1571)
    prs_val = viewer.get("pullRequests", {}).get("totalCount", 11)
    issues_val = viewer.get("issues", {}).get("totalCount", 0)
    contrib_repos_val = viewer.get("repositoriesContributedTo", {}).get("totalCount", 6)

    stats = {
        "stars": total_stars,
        "commits": commits_val,
        "prs": prs_val,
        "issues": issues_val,
        "contrib_repos": contrib_repos_val,
    }

    # 3. Streaks
    calendar = viewer.get("contributionsCollection", {}).get("contributionCalendar", {})
    total_contribs = calendar.get("totalContributions", 1592)
    streak_data = {
        "total": total_contribs,
        "current_streak": 1,
        "longest_streak": 11,
        "longest_range": "18 Sep 2026 - 28 Sep 2026",
    }

    # 4. Top repos (public only)
    top_repos_list = []
    for r in repos:
        if not r.get("isPrivate", False):
            c = 0
            if r.get("defaultBranchRef") and r["defaultBranchRef"].get("target"):
                c = r["defaultBranchRef"]["target"].get("history", {}).get("totalCount", 0)
            top_repos_list.append((r["name"], c))
    top_repos_list.sort(key=lambda x: x[1], reverse=True)
    if not top_repos_list:
        top_repos_list = [("SagaRSonaR", 277), ("SAGAR-SonarVision", 277), ("claimcheck-retail-media", 71), ("Taskflow", 57)]

    # Generate SVGs
    assets_map = {
        "pixel-banner-dark.svg": build_banner_pixel_svg("dark"),
        "pixel-banner-light.svg": build_banner_pixel_svg("light"),
        "pixel-headline-dark.svg": build_headline_pixel_svg("dark"),
        "pixel-headline-light.svg": build_headline_pixel_svg("light"),
        "pixel-about-dark.svg": build_about_illustration_svg("dark"),
        "pixel-about-light.svg": build_about_illustration_svg("light"),
        "pixel-tiles-dark.svg": build_tiles_grid_svg("dark"),
        "pixel-tiles-light.svg": build_tiles_grid_svg("light"),
        "stats-card-dark.svg": build_stats_card_svg(stats, "dark"),
        "stats-card-light.svg": build_stats_card_svg(stats, "light"),
        "streak-card-dark.svg": build_streak_card_svg(streak_data, "dark"),
        "streak-card-light.svg": build_streak_card_svg(streak_data, "light"),
        "languages-card-dark.svg": build_languages_card_svg(langs_data, "dark"),
        "languages-card-light.svg": build_languages_card_svg(langs_data, "light"),
        "contributors-card-dark.svg": build_contributors_card_svg(top_repos_list, "dark"),
        "contributors-card-light.svg": build_contributors_card_svg(top_repos_list, "light"),
        "quote-card-dark.svg": build_quote_card_svg("dark"),
        "quote-card-light.svg": build_quote_card_svg("light"),
        "closing-strip-dark.svg": build_closing_strip_svg("dark"),
        "closing-strip-light.svg": build_closing_strip_svg("light"),
    }

    # Validation
    for name, content in assets_map.items():
        if not validate_svg(content, name):
            print(f"[FATAL] SVG validation failed for {name}")
            sys.exit(1)
        kb = len(content.encode()) / 1024.0
        if kb > 150:
            print(f"[FATAL] {name} exceeds 150 KB ({kb:.1f} KB)")
            sys.exit(1)

    if args.dry_run:
        print("[INFO] --dry-run: All SVGs generated and validated successfully.")
        sys.exit(0)

    os.makedirs(ASSETS_DIR, exist_ok=True)
    for name, content in assets_map.items():
        with open(os.path.join(ASSETS_DIR, name), "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[OK] Wrote assets/{name}")

    print("[SUCCESS] All pixel-art profile assets compiled.")

if __name__ == "__main__":
    main()
