# Profile Automation Architecture

The `narayan-nkj` profile README updates autonomously without manual maintenance or third-party hosted widgets.

---

## 1. Cadence & Why 6-Hour Intervals

The automation triggers every 6 hours at an off-peak odd minute (`17 */6 * * *`):
1. **API Rate Preservation:** Spreads queries evenly across GitHub's secondary rate-limit windows.
2. **Deterministic Freshness:** Keeps project push telemetry and recent ship logs current within quarter-day precision.
3. **Ghost Inactivity Prevention:** GitHub halts scheduled workflows in public repositories after 60 days of repository dormancy. A 7-day heartbeat mechanism guarantees an autonomous commit occurs before the 60-day deadline, keeping scheduled jobs perpetually active.

---

## 2. Core Components

* `profile.config.json`: Single source of truth for portfolio projects, allowed domains, timezone, and star thresholds.
* `scripts/build_assets.py`: Standard-library Python engine (`urllib`, `json`, `zoneinfo`, `hashlib`, `xml.etree`).
  - Fetches GraphQL languages and contribution calendar.
  - Queries GitHub public events for 30-day activity radar and recently shipped activities.
  - Renders dual-mode (`-dark.svg` and `-light.svg`) vector assets with system fonts and reduced-motion static fallback.
  - Generates cache-busting `?v=<8-char-hash>` query strings.
* `.github/workflows/update-profile.yml`: Pinned Actions workflow executing on schedule, manual dispatch, and configuration push.
* `.github/workflows/tests.yml`: Automated CI testing suite for PRs and pushes.

---

## 3. How to Add a Project

To add a featured engineering project to your profile:
1. Open `profile.config.json`.
2. Append a new object to `"featured_projects"`:
   ```json
   {
     "repo": "narayan-nkj/your-new-repo",
     "custom_description": "Hand-written one-sentence description.",
     "extra_links": [
       { "label": "Live Demo", "url": "https://your-demo.vercel.app" }
     ]
   }
   ```
3. Commit and push `profile.config.json`. The GitHub Actions workflow will automatically pull metadata, primary language, and latest push timestamp.

---

## 4. Local Testing & Debugging

Run deterministic tests locally without hitting GitHub API limits:
```bash
# Deterministic offline run using fixtures
python3 scripts/build_assets.py --offline

# Dry-run validation (validates SVGs and markers without writing)
python3 scripts/build_assets.py --dry-run

# Run full automated test suite
python3 -m unittest discover tests
```

---

## 5. Re-enabling a Disabled Schedule

If GitHub disables scheduled runs due to 60 days of inactivity:
1. Visit repository **Actions** tab on GitHub: `https://github.com/narayan-nkj/narayan-nkj/actions`.
2. Select **Update Profile Assets** workflow.
3. Click **Enable workflow** (or click **Run workflow** to trigger an immediate run).
