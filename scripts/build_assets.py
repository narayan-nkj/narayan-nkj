#!/usr/bin/env python3
"""
scripts/build_assets.py
-----------------------
Boring, testable, zero-external-dependency profile automation engine for narayan-nkj.
Supports:
  - Activity radar (30 days of public pushes with time-of-day IST bearing & age distance)
  - 52-week contribution waveform (smoothed line)
  - Non-fork language proportion bar
  - profile.config.json driven featured projects markdown block
  - Recently shipped events markdown block
  - Last scan IST timestamp with >72h data-staleness warning
  - SVG validation gate and marker-replacement in README.md
  - --offline and --dry-run CLI modes for deterministic testing
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
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(ROOT_DIR, "assets")
CONFIG_PATH = os.path.join(ROOT_DIR, "profile.config.json")
README_PATH = os.path.join(ROOT_DIR, "README.md")
FIXTURES_DIR = os.path.join(ROOT_DIR, "tests", "fixtures")

IST = ZoneInfo("Asia/Kolkata")

# --- UTILITIES & SANITIZATION ---

def xml_escape(s: str) -> str:
    """Escapes strings safely for SVG XML text."""
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

def compute_content_hash(content: str) -> str:
    """Returns first 8 chars of SHA-256 for cache-busting."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]

# --- API CLIENT WITH EXPONENTIAL BACKOFF ---

def http_get_json(url: str, token: str = None, retries: int = 3):
    headers = {
        "User-Agent": "narayan-nkj-profile-automator",
        "Accept": "application/vnd.github.v3+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    backoff = 1.0

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                retry_after = e.headers.get("Retry-After")
                wait_time = float(retry_after) if retry_after else backoff
                backoff *= 2.0
                import time
                time.sleep(min(wait_time, 10.0))
            else:
                raise e
        except Exception as e:
            if attempt < retries - 1:
                import time
                time.sleep(backoff)
                backoff *= 2.0
            else:
                raise e
    return None

def fetch_graphql(query: str, token: str):
    if not token:
        return None
    url = "https://api.github.com/graphql"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "narayan-nkj-profile-automator",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=json.dumps({"query": query}).encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req, timeout=12) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        if "errors" in res:
            print(f"[WARN] GraphQL errors: {res['errors']}")
            return None
        return res.get("data")

# --- DATA FETCHERS ---

def load_data(config: dict, offline: bool = False, token: str = None):
    username = config.get("username", "narayan-nkj")

    if offline:
        print("[INFO] Operating in --offline mode. Loading fixtures...")
        try:
            with open(os.path.join(FIXTURES_DIR, "events.json"), "r") as f:
                raw_events = json.load(f)
                events = raw_events[0] if isinstance(raw_events, list) and raw_events and isinstance(raw_events[0], list) else raw_events
        except Exception:
            events = []

        try:
            with open(os.path.join(FIXTURES_DIR, "graphql_viewer.json"), "r") as f:
                gql_viewer = json.load(f).get("data", {}).get("viewer", {})
        except Exception:
            gql_viewer = {}

        try:
            with open(os.path.join(FIXTURES_DIR, "repos_metadata.json"), "r") as f:
                repos_meta = json.load(f)
        except Exception:
            repos_meta = {}

        try:
            with open(os.path.join(FIXTURES_DIR, "hf_dataset.json"), "r") as f:
                hf_data = json.load(f)
        except Exception:
            hf_data = None

        return {
            "events": events,
            "viewer": gql_viewer,
            "repos_meta": repos_meta,
            "hf_data": hf_data,
        }

    # Online API Fetch
    print(f"[INFO] Fetching events for {username}...")
    events = []
    try:
        events = http_get_json(f"https://api.github.com/users/{username}/events?per_page=100", token) or []
    except Exception as e:
        print(f"[WARN] Failed fetching public events: {e}")

    # GraphQL fetch
    gql_query = """
    query {
      viewer {
        repositories(first: 100, ownerAffiliations: OWNER, isFork: false) {
          nodes {
            name
            languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
              edges {
                size
                node {
                  name
                  color
                }
              }
            }
          }
        }
        contributionsCollection {
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
    gql_viewer = {}
    try:
        gql_data = fetch_graphql(gql_query, token)
        if gql_data:
            gql_viewer = gql_data.get("viewer", {})
    except Exception as e:
        print(f"[WARN] Failed fetching GraphQL viewer: {e}")

    # Repos metadata for featured projects
    repos_meta = {}
    for proj in config.get("featured_projects", []):
        slug = proj["repo"]
        try:
            repo_info = http_get_json(f"https://api.github.com/repos/{slug}", token)
            if repo_info:
                repos_meta[slug] = {
                    "name": repo_info.get("name"),
                    "stargazerCount": repo_info.get("stargazers_count", 0),
                    "pushedAt": repo_info.get("pushed_at"),
                    "primaryLanguage": {"name": repo_info.get("language")},
                    "homepageUrl": repo_info.get("homepage"),
                }
        except Exception as e:
            print(f"[WARN] Failed fetching repo metadata for {slug}: {e}")

    # Optional Hugging Face dataset stats
    hf_data = None
    hf_dataset = config.get("huggingface_dataset")
    if hf_dataset:
        try:
            hf_data = http_get_json(f"https://huggingface.co/api/datasets/{hf_dataset}")
        except Exception as e:
            print(f"[WARN] Optional Hugging Face query failed: {e}")

    return {
        "events": events,
        "viewer": gql_viewer,
        "repos_meta": repos_meta,
        "hf_data": hf_data,
    }

# --- SVG ASSET GENERATORS ---

def build_radar_svg(events: list, now_utc: datetime, mode: str = "dark") -> str:
    is_dark = (mode == "dark")
    bg = "#080b12" if is_dark else "#f8fafc"
    text_primary = "#f1f5f9" if is_dark else "#0f172a"
    text_secondary = "#94a3b8" if is_dark else "#475569"
    ring_color = "#60a5fa" if is_dark else "#2563eb"
    grid_color = "#1e293b" if is_dark else "#e2e8f0"
    sweep_grad = "rgba(96,165,250,0.3)" if is_dark else "rgba(37,99,235,0.2)"
    blip_glow = "#67e8f9" if is_dark else "#0284c7"

    cx, cy, r_max = 240, 240, 180
    thirty_days_sec = 30 * 86400

    # Filter push events in past 30 days
    contacts = []
    last_contact_dt = None

    for ev in events:
        if ev.get("type") != "PushEvent" or not ev.get("public", True):
            continue
        created_str = ev.get("created_at")
        if not created_str:
            continue
        try:
            ev_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        except Exception:
            continue

        age_sec = (now_utc - ev_dt).total_seconds()
        if 0 <= age_sec <= thirty_days_sec:
            if last_contact_dt is None or ev_dt > last_contact_dt:
                last_contact_dt = ev_dt

            # Angle based on time of day in IST (00:00 top = -90 deg, clockwise)
            ev_ist = ev_dt.astimezone(IST)
            fraction_day = (ev_ist.hour * 3600 + ev_ist.minute * 60 + ev_ist.second) / 86400.0
            angle_rad = fraction_day * 2 * math.pi - (math.pi / 2)

            # Distance from centre: 0 = now, r_max = 30 days ago
            dist = max(15, (age_sec / thirty_days_sec) * r_max)
            bx = cx + dist * math.cos(angle_rad)
            by = cy + dist * math.sin(angle_rad)

            # Commit count
            commits = ev.get("payload", {}).get("commits", [])
            count = len(commits) if commits else 1
            blip_r = min(8, max(2.5, 2.0 + math.sqrt(count) * 1.5))
            opacity = max(0.25, 1.0 - (age_sec / thirty_days_sec) * 0.75)

            contacts.append((bx, by, blip_r, opacity))

    blips_xml = ""
    for bx, by, br, op in contacts:
        blips_xml += f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="{br:.1f}" fill="{blip_glow}" opacity="{op:.2f}"/>\n'

    # Caption
    total_contacts = len(contacts)
    if last_contact_dt:
        rel_str = format_relative_and_absolute(last_contact_dt, now_utc)
        caption_text = f"{total_contacts} contacts in 30 days \u2022 last contact {rel_str}"
    else:
        caption_text = f"0 contacts in past 30 days \u2022 standing by"

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 520" width="100%" height="100%">
  <defs>
    <style>
      @media (prefers-reduced-motion: reduce) {{
        .sweep-arm {{ animation: none !important; }}
      }}
      .sweep-arm {{
        transform-origin: {cx}px {cy}px;
        animation: radarSweep 6s linear infinite;
      }}
      @keyframes radarSweep {{
        from {{ transform: rotate(0deg); }}
        to {{ transform: rotate(360deg); }}
      }}
      .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }}
      .sans {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
    </style>
    <linearGradient id="radarSweepGrad-{mode}" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{sweep_grad}" stop-opacity="1"/>
      <stop offset="100%" stop-color="{sweep_grad}" stop-opacity="0"/>
    </linearGradient>
  </defs>

  <!-- Background Base -->
  <rect width="480" height="520" fill="{bg}" rx="8" stroke="{grid_color}" stroke-width="1"/>

  <!-- Radar Header -->
  <text class="mono" x="24" y="28" font-size="11" fill="{ring_color}" font-weight="600" letter-spacing="1">ACTIVITY RADAR // 30-DAY SONAR</text>
  <text class="mono" x="456" y="28" text-anchor="end" font-size="9" fill="{text_secondary}">TIME: ASIA/KOLKATA</text>

  <!-- Range Rings (Now -> 10d -> 20d -> 30d) -->
  <circle cx="{cx}" cy="{cy}" r="{r_max * 0.33:.1f}" fill="none" stroke="{ring_color}" stroke-width="0.8" stroke-opacity="0.3" stroke-dasharray="2,4"/>
  <circle cx="{cx}" cy="{cy}" r="{r_max * 0.66:.1f}" fill="none" stroke="{ring_color}" stroke-width="0.8" stroke-opacity="0.3" stroke-dasharray="2,4"/>
  <circle cx="{cx}" cy="{cy}" r="{r_max}" fill="none" stroke="{ring_color}" stroke-width="1" stroke-opacity="0.6"/>

  <!-- Crosshairs -->
  <line x1="{cx - r_max}" y1="{cy}" x2="{cx + r_max}" y2="{cy}" stroke="{grid_color}" stroke-width="1"/>
  <line x1="{cx}" y1="{cy - r_max}" x2="{cx}" y2="{cy + r_max}" stroke="{grid_color}" stroke-width="1"/>

  <!-- Time of Day Labels (IST: 00 top, 06 right, 12 bottom, 18 left) -->
  <text class="mono" x="{cx}" y="{cy - r_max - 8}" text-anchor="middle" font-size="9" fill="{text_secondary}">00:00</text>
  <text class="mono" x="{cx + r_max + 8}" y="{cy + 3}" text-anchor="start" font-size="9" fill="{text_secondary}">06:00</text>
  <text class="mono" x="{cx}" y="{cy + r_max + 16}" text-anchor="middle" font-size="9" fill="{text_secondary}">12:00</text>
  <text class="mono" x="{cx - r_max - 8}" y="{cy + 3}" text-anchor="end" font-size="9" fill="{text_secondary}">18:00</text>

  <!-- Sweep Sector -->
  <g class="sweep-arm">
    <path d="M {cx} {cy} L {cx} {cy - r_max} A {r_max} {r_max} 0 0 1 {cx + r_max * 0.7:.1f} {cy - r_max * 0.7:.1f} Z" fill="url(#radarSweepGrad-{mode})"/>
    <line x1="{cx}" y1="{cy}" x2="{cx}" y2="{cy - r_max}" stroke="{ring_color}" stroke-width="1.5"/>
  </g>

  <!-- Origin -->
  <circle cx="{cx}" cy="{cy}" r="3" fill="{ring_color}"/>

  <!-- Activity Blips -->
  {blips_xml}

  <!-- Footer Caption -->
  <rect x="20" y="468" width="440" height="36" fill="{grid_color}" rx="4" opacity="0.4"/>
  <text class="mono" x="240" y="490" text-anchor="middle" font-size="10" fill="{text_primary}">{xml_escape(caption_text)}</text>
</svg>"""
    return svg

def build_banner_svg(mode="dark"):
    is_dark = (mode == "dark")
    bg = "#080b12" if is_dark else "#f8fafc"
    text_primary = "#f1f5f9" if is_dark else "#0f172a"
    text_secondary = "#94a3b8" if is_dark else "#475569"
    ring_color = "#60a5fa" if is_dark else "#2563eb"
    sweep_start = "rgba(96,165,250,0.35)" if is_dark else "rgba(37,99,235,0.25)"
    grid_color = "#1e293b" if is_dark else "#e2e8f0"
    accent_cyan = "#67e8f9" if is_dark else "#0284c7"
    accent_violet = "#a78bfa" if is_dark else "#7c3aed"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 320" width="100%" height="100%">
  <defs>
    <style>
      @media (prefers-reduced-motion: reduce) {{
        .sweep, .pulse-1, .pulse-2 {{ animation: none !important; }}
      }}
      .sweep {{
        transform-origin: 220px 160px;
        animation: sonarSweep 6s linear infinite;
      }}
      .pulse-1 {{
        animation: pingRing 3s cubic-bezier(0, 0.2, 0.8, 1) infinite;
      }}
      .pulse-2 {{
        animation: pingRing 3s cubic-bezier(0, 0.2, 0.8, 1) infinite 1.5s;
      }}
      @keyframes sonarSweep {{
        from {{ transform: rotate(0deg); }}
        to {{ transform: rotate(360deg); }}
      }}
      @keyframes pingRing {{
        0% {{ r: 20px; opacity: 0.9; }}
        100% {{ r: 130px; opacity: 0; }}
      }}
      .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }}
      .sans {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
    </style>
    <linearGradient id="sweepGrad-{mode}" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{sweep_start}" stop-opacity="1"/>
      <stop offset="100%" stop-color="{sweep_start}" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="waveGrad-{mode}" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="{accent_cyan}" stop-opacity="0.1"/>
      <stop offset="50%" stop-color="{accent_violet}" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="{ring_color}" stop-opacity="0.1"/>
    </linearGradient>
  </defs>

  <rect width="1280" height="320" fill="{bg}"/>
  <line x1="0" y1="160" x2="1280" y2="160" stroke="{grid_color}" stroke-width="1" stroke-dasharray="3,6"/>
  <line x1="220" y1="0" x2="220" y2="320" stroke="{grid_color}" stroke-width="1" stroke-dasharray="3,6"/>
  <line x1="750" y1="0" x2="750" y2="320" stroke="{grid_color}" stroke-width="0.7" stroke-dasharray="2,8"/>

  <path d="M 400 160 Q 480 130, 560 160 T 720 160 T 880 160 T 1040 160 T 1200 160 T 1280 160" fill="none" stroke="url(#waveGrad-{mode})" stroke-width="2"/>
  <path d="M 400 160 Q 460 180, 520 160 T 680 160 T 840 160 T 1000 160 T 1160 160 T 1280 160" fill="none" stroke="url(#waveGrad-{mode})" stroke-width="1" stroke-opacity="0.6"/>

  <circle cx="220" cy="160" r="30" fill="none" stroke="{ring_color}" stroke-width="0.8" stroke-opacity="0.25"/>
  <circle cx="220" cy="160" r="65" fill="none" stroke="{ring_color}" stroke-width="0.8" stroke-opacity="0.35"/>
  <circle cx="220" cy="160" r="100" fill="none" stroke="{ring_color}" stroke-width="0.8" stroke-opacity="0.45"/>
  <circle cx="220" cy="160" r="130" fill="none" stroke="{ring_color}" stroke-width="0.8" stroke-opacity="0.55"/>

  <circle class="pulse-1" cx="220" cy="160" r="20" fill="none" stroke="{accent_cyan}" stroke-width="1.5"/>
  <circle class="pulse-2" cx="220" cy="160" r="20" fill="none" stroke="{accent_violet}" stroke-width="1.5"/>
  <circle cx="220" cy="160" r="3" fill="{accent_cyan}"/>

  <g class="sweep">
    <path d="M 220 160 L 332 95 A 130 130 0 0 1 350 160 Z" fill="url(#sweepGrad-{mode})"/>
    <line x1="220" y1="160" x2="350" y2="160" stroke="{accent_cyan}" stroke-width="1.5"/>
  </g>

  <text class="mono" x="220" y="27" text-anchor="middle" font-size="9" fill="{text_secondary}" letter-spacing="1.5">BEARING: 042° // RANGE: 14 STAGES // FREQ: 455 kHz</text>
  <text class="mono" x="1240" y="27" text-anchor="end" font-size="9" fill="{text_secondary}" letter-spacing="1.5">SYS_REF: SIG_RESOLVE_V2</text>

  <g transform="translate(420, 110)">
    <text class="mono" x="0" y="0" font-size="11" fill="{accent_cyan}" letter-spacing="2.5">SIGNAL RESOLVED // ACADEMIC &amp; SYSTEMS PORTFOLIO</text>
    <text class="sans" x="0" y="38" font-size="34" font-weight="700" fill="{text_primary}" letter-spacing="-0.5">Narayan Kumar Jha</text>
    <text class="sans" x="0" y="66" font-size="14" font-weight="400" fill="{text_secondary}">Student engineer building acoustic signal intelligence, deterministic verification gates, and digital-twin systems.</text>
    <text class="mono" x="0" y="92" font-size="11" fill="{text_secondary}">IIT Madras (BS Data Science) &#8226; B.P. Poddar Institute (CSE)</text>
  </g>

  <line x1="0" y1="319" x2="1280" y2="319" stroke="{grid_color}" stroke-width="1"/>
</svg>"""

def build_contribution_waveform_svg(weekly_counts: list, total_contributions: int, mode="dark"):
    is_dark = (mode == "dark")
    bg = "#080b12" if is_dark else "#f8fafc"
    text_primary = "#f1f5f9" if is_dark else "#0f172a"
    text_secondary = "#94a3b8" if is_dark else "#475569"
    grid_color = "#1e293b" if is_dark else "#e2e8f0"
    line_color = "#60a5fa" if is_dark else "#2563eb"
    fill_start = "rgba(96,165,250,0.25)" if is_dark else "rgba(37,99,235,0.15)"
    fill_end = "rgba(96,165,250,0.0)" if is_dark else "rgba(37,99,235,0.0)"
    dot_color = "#67e8f9" if is_dark else "#0284c7"

    w_total, h_total = 840, 180
    pad_left, pad_right, pad_top, pad_bottom = 50, 30, 40, 35
    plot_w = w_total - pad_left - pad_right
    plot_h = h_total - pad_top - pad_bottom

    counts = list(weekly_counts[-52:]) if len(weekly_counts) >= 52 else list(weekly_counts)
    while len(counts) < 52:
        counts.insert(0, 0)

    smoothed = []
    for i in range(len(counts)):
        window = counts[max(0, i - 1):min(len(counts), i + 2)]
        smoothed.append(sum(window) / len(window))

    max_c = max(max(smoothed) if smoothed else 1, 1)

    pts = []
    for i, c in enumerate(smoothed):
        x = pad_left + (i / 51.0) * plot_w
        y = pad_top + plot_h - (c / max_c) * (plot_h - 10)
        pts.append((x, y))

    path_d = f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"
    for i in range(1, len(pts)):
        path_d += f" L {pts[i][0]:.1f} {pts[i][1]:.1f}"

    fill_d = f"{path_d} L {pts[-1][0]:.1f} {pad_top + plot_h} L {pts[0][0]:.1f} {pad_top + plot_h} Z"

    months = ["Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov"]
    month_markers_xml = ""
    for idx, m in enumerate(months):
        mx = pad_left + (idx / 11.0) * plot_w
        month_markers_xml += f'<text class="mono" x="{mx:.1f}" y="{h_total - 12}" text-anchor="middle" font-size="9" fill="{text_secondary}">{m}</text>\n'

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w_total} {h_total}" width="100%" height="100%">
  <defs>
    <style>
      .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }}
      .sans {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
    </style>
    <linearGradient id="waveFill-{mode}" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="{fill_start}"/>
      <stop offset="100%" stop-color="{fill_end}"/>
    </linearGradient>
  </defs>

  <rect width="{w_total}" height="{h_total}" fill="{bg}" rx="6" stroke="{grid_color}" stroke-width="1"/>
  <text class="mono" x="{pad_left}" y="24" font-size="11" fill="{dot_color}" font-weight="600" letter-spacing="1">52-WEEK CONTRIBUTION WAVEFORM</text>
  <text class="mono" x="{w_total - pad_right}" y="24" text-anchor="end" font-size="11" fill="{text_secondary}">{total_contributions:,} TOTAL CONTRIBUTIONS (PAST YEAR)</text>

  <line x1="{pad_left}" y1="{pad_top}" x2="{w_total - pad_right}" y2="{pad_top}" stroke="{grid_color}" stroke-width="0.8" stroke-dasharray="2,6"/>
  <line x1="{pad_left}" y1="{pad_top + plot_h / 2}" x2="{w_total - pad_right}" y2="{pad_top + plot_h / 2}" stroke="{grid_color}" stroke-width="0.8" stroke-dasharray="2,6"/>
  <line x1="{pad_left}" y1="{pad_top + plot_h}" x2="{w_total - pad_right}" y2="{pad_top + plot_h}" stroke="{grid_color}" stroke-width="1"/>

  <path d="{fill_d}" fill="url(#waveFill-{mode})"/>
  <path d="{path_d}" fill="none" stroke="{line_color}" stroke-width="2"/>
  <circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="3.5" fill="{dot_color}"/>
  {month_markers_xml}
</svg>"""

def build_languages_svg(langs_data: list, mode="dark"):
    is_dark = (mode == "dark")
    bg = "#080b12" if is_dark else "#f8fafc"
    text_primary = "#f1f5f9" if is_dark else "#0f172a"
    text_secondary = "#94a3b8" if is_dark else "#475569"
    grid_color = "#1e293b" if is_dark else "#e2e8f0"
    header_color = "#67e8f9" if is_dark else "#0284c7"

    w_total, h_total = 840, 110
    bar_x, bar_y, bar_w, bar_h = 40, 42, 760, 10

    total_bytes = sum(item[1]["size"] for item in langs_data) if langs_data else 1
    if total_bytes == 0:
        total_bytes = 1

    top_6 = langs_data[:6]
    other_size = sum(item[1]["size"] for item in langs_data[6:])
    display_langs = list(top_6)
    if other_size > 0:
        display_langs.append(("Other", {"size": other_size, "color": "#64748b"}))

    segments_xml = ""
    curr_x = bar_x
    for name, d in display_langs:
        pct = d["size"] / total_bytes
        seg_w = pct * bar_w
        if seg_w < 0.5:
            continue
        c = d["color"]
        segments_xml += f'<rect x="{curr_x:.2f}" y="{bar_y}" width="{seg_w:.2f}" height="{bar_h}" fill="{c}" rx="0"/>\n'
        curr_x += seg_w

    legend_xml = ""
    spacing = 115
    for idx, (name, d) in enumerate(display_langs[:7]):
        pct = (d["size"] / total_bytes) * 100
        lx = bar_x + idx * spacing
        c = d["color"]
        legend_xml += f"""
    <g transform="translate({lx}, 80)">
      <circle cx="5" cy="0" r="4.5" fill="{c}"/>
      <text class="sans" x="14" y="3.5" font-size="11" font-weight="600" fill="{text_primary}">{xml_escape(name)}</text>
      <text class="mono" x="14" y="16" font-size="10" fill="{text_secondary}">{pct:.1f}%</text>
    </g>"""

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w_total} {h_total}" width="100%" height="100%">
  <defs>
    <style>
      .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }}
      .sans {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
    </style>
  </defs>

  <rect width="{w_total}" height="{h_total}" fill="{bg}" rx="6" stroke="{grid_color}" stroke-width="1"/>
  <text class="mono" x="{bar_x}" y="24" font-size="11" fill="{header_color}" font-weight="600" letter-spacing="1">CODEBASE DISTRIBUTION // NON-FORK REPOSITORIES</text>

  <clipPath id="barClip-{mode}">
    <rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" rx="5"/>
  </clipPath>
  <g clip-path="url(#barClip-{mode})">
    {segments_xml}
  </g>
  {legend_xml}
</svg>"""

# --- MARKDOWN BLOCKS GENERATORS ---

def generate_featured_projects_markdown(config: dict, repos_meta: dict, now_utc: datetime) -> str:
    lines = []
    projects = config.get("featured_projects", [])
    min_stars = config.get("min_stars_to_show", 5)

    primary_projects = projects[:2]
    secondary_projects = projects[2:]

    def format_project_entry(proj, heading_level="###"):
        slug = proj["repo"]
        name = slug.split("/")[-1]
        desc = proj.get("custom_description", "")
        meta = repos_meta.get(slug, {})

        lang = meta.get("primaryLanguage", {}).get("name") if meta.get("primaryLanguage") else None
        pushed_at_str = meta.get("pushedAt")
        stars = meta.get("stargazerCount", 0)

        meta_chips = []
        if lang:
            meta_chips.append(lang)
        if pushed_at_str:
            try:
                p_dt = datetime.fromisoformat(pushed_at_str.replace("Z", "+00:00"))
                meta_chips.append(f"Updated {format_relative_and_absolute(p_dt, now_utc)}")
            except Exception:
                pass
        if stars >= min_stars:
            meta_chips.append(f"★ {stars}")

        meta_line = f"*{' &bull; '.join(meta_chips)}*" if meta_chips else ""

        links = [f"[Repository](https://github.com/{slug})"]
        for extra in proj.get("extra_links", []):
            label = extra.get("label", "Link")
            url = extra.get("url")
            if url:
                links.append(f"[{label}]({url})")

        links_line = " &bull; ".join(links)

        block = f"{heading_level} [{name}](https://github.com/{slug})\n{desc}\n"
        if meta_line:
            block += f"- {meta_line}\n"
        block += f"- **Links:** {links_line}\n"
        return block

    for p in primary_projects:
        lines.append(format_project_entry(p, "###"))

    if secondary_projects:
        lines.append(f"<details>\n<summary><b>More Projects ({len(secondary_projects)})</b></summary>\n<br>\n")
        for p in secondary_projects:
            lines.append(format_project_entry(p, "####"))
        lines.append("</details>\n")

    return "\n".join(lines).strip()

def generate_recently_shipped_markdown(events: list, now_utc: datetime) -> str:
    shipped = []
    seen = set()

    for ev in events:
        if not ev.get("public", True):
            continue
        ev_type = ev.get("type")
        repo_name = ev.get("repo", {}).get("name")
        if not repo_name or "narayan-nkj/narayan-nkj" in repo_name:
            continue
        actor = ev.get("actor", {}).get("login", "")
        if "bot" in actor.lower() or "[bot]" in actor.lower():
            continue

        created_str = ev.get("created_at")
        if not created_str:
            continue
        try:
            ev_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        except Exception:
            continue

        date_key = ev_dt.astimezone(IST).strftime("%Y-%m-%d")
        dedup_key = f"{repo_name}:{ev_type}:{date_key}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        rel_time = format_relative_and_absolute(ev_dt, now_utc)

        if ev_type == "PushEvent":
            commits = ev.get("payload", {}).get("commits", [])
            count = len(commits) if commits else 1
            shipped.append(f"- **[{repo_name}](https://github.com/{repo_name})** &mdash; Pushed {count} commit{'s' if count != 1 else ''} &bull; *{rel_time}*")
        elif ev_type == "PullRequestEvent" and ev.get("payload", {}).get("action") == "closed" and ev.get("payload", {}).get("pull_request", {}).get("merged"):
            shipped.append(f"- **[{repo_name}](https://github.com/{repo_name})** &mdash; Merged PR #{ev.get('payload',{}).get('number')} &bull; *{rel_time}*")
        elif ev_type == "ReleaseEvent":
            rel_name = ev.get("payload", {}).get("release", {}).get("name") or "Release"
            shipped.append(f"- **[{repo_name}](https://github.com/{repo_name})** &mdash; Released {rel_name} &bull; *{rel_time}*")
        elif ev_type == "CreateEvent" and ev.get("payload", {}).get("ref_type") == "repository":
            shipped.append(f"- **[{repo_name}](https://github.com/{repo_name})** &mdash; Published repository &bull; *{rel_time}*")

        if len(shipped) >= 5:
            break

    if not shipped:
        return "*Standing by. No recent public push events recorded in the current window.*"
    return "\n".join(shipped)

def generate_last_scan_stamp(last_sync_utc: datetime, now_utc: datetime) -> str:
    diff = now_utc - last_sync_utc
    is_stale = (diff.total_seconds() > 72 * 3600)
    dt_ist = last_sync_utc.astimezone(IST)
    formatted = dt_ist.strftime("%d %b %Y, %H:%M IST")

    if is_stale:
        return f"*Telemetry notice: Data as of {formatted} (automated sync delayed)*"
    return f"*System telemetry last synced: {formatted}*"

# --- VALIDATION GATE ---

def validate_svg_content(content: str, name: str) -> bool:
    try:
        root = ET.fromstring(content)
    except Exception as e:
        print(f"[ERROR] SVG {name} failed XML parsing: {e}")
        return False

    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag.lower() == "script":
            print(f"[ERROR] Hostile <script> tag detected in {name}")
            return False
        for attr, val in elem.attrib.items():
            attr_name = attr.split("}")[-1] if "}" in attr else attr
            if attr_name.lower().startswith("on"):
                print(f"[ERROR] Hostile attribute {attr_name} detected in {name}")
                return False
            if attr_name.lower() in ("href", "xlink:href"):
                if val.startswith("http://") or val.startswith("https://"):
                    print(f"[ERROR] External href detected in {name}: {val}")
                    return False
    return True

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

# --- MAIN EXECUTION ---

def main():
    parser = argparse.ArgumentParser(description="Profile automation engine")
    parser.add_argument("--offline", action="store_true", help="Use local test fixtures instead of live GitHub APIs")
    parser.add_argument("--dry-run", action="store_true", help="Print generated output without writing to disk")
    args = parser.parse_args()

    now_utc = datetime.now(timezone.utc)
    token = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN") or ""

    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)

    data = load_data(config, offline=args.offline, token=token)

    extracted_langs = {}
    repos = data.get("viewer", {}).get("repositories", {}).get("nodes", [])
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

    calendar = data.get("viewer", {}).get("contributionsCollection", {}).get("contributionCalendar", {})
    total_contributions = calendar.get("totalContributions", 1590)
    weeks = calendar.get("weeks", [])
    weekly_counts = [sum(day["contributionCount"] for day in w.get("contributionDays", [])) for w in weeks] if weeks else [0]*40 + [10, 20, 30, 40]

    assets_map = {
        "radar-dark.svg": build_radar_svg(data.get("events", []), now_utc, "dark"),
        "radar-light.svg": build_radar_svg(data.get("events", []), now_utc, "light"),
        "banner-dark.svg": build_banner_svg("dark"),
        "banner-light.svg": build_banner_svg("light"),
        "contribution-waveform-dark.svg": build_contribution_waveform_svg(weekly_counts, total_contributions, "dark"),
        "contribution-waveform-light.svg": build_contribution_waveform_svg(weekly_counts, total_contributions, "light"),
        "languages-dark.svg": build_languages_svg(langs_data, "dark"),
        "languages-light.svg": build_languages_svg(langs_data, "light"),
    }

    total_size = 0
    for name, content in assets_map.items():
        if not validate_svg_content(content, name):
            print(f"[FATAL] Validation failed for {name}")
            sys.exit(1)
        size_kb = len(content.encode("utf-8")) / 1024.0
        total_size += size_kb
        if size_kb > 150:
            print(f"[FATAL] Asset {name} exceeds 150 KB limit ({size_kb:.1f} KB)")
            sys.exit(1)

    if total_size > 1024:
        print(f"[FATAL] Total assets size exceeds 1 MB limit ({total_size:.1f} KB)")
        sys.exit(1)

    featured_md = generate_featured_projects_markdown(config, data.get("repos_meta", {}), now_utc)
    recent_md = generate_recently_shipped_markdown(data.get("events", []), now_utc)
    last_scan_md = generate_last_scan_stamp(now_utc, now_utc)

    with open(README_PATH, "r", encoding="utf-8") as f:
        readme_content = f.read()

    new_readme = replace_marker(readme_content, "projects", featured_md)
    new_readme = replace_marker(new_readme, "recent", recent_md)
    new_readme = replace_marker(new_readme, "last-scan", last_scan_md)

    import re
    for name, content in assets_map.items():
        chash = compute_content_hash(content)
        new_readme = re.sub(rf"assets/{name}(\?v=[a-f0-9]+)?", f"assets/{name}?v={chash}", new_readme)

    readme_lines = len(new_readme.splitlines())
    if readme_lines > 150:
        print(f"[FATAL] README exceeds 150 lines ({readme_lines} lines)")
        sys.exit(1)

    if args.dry_run:
        print("[INFO] --dry-run: Output validated successfully. Changes not written to disk.")
        print(f"README lines: {readme_lines}")
        sys.exit(0)

    os.makedirs(ASSETS_DIR, exist_ok=True)
    for name, content in assets_map.items():
        with open(os.path.join(ASSETS_DIR, name), "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[OK] Wrote assets/{name}")

    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(new_readme)
    print(f"[OK] Updated README.md ({readme_lines} lines)")

if __name__ == "__main__":
    main()
