# Back4App Container Auto-Redeploy

Automatically logs into Back4App via GitHub OAuth and clicks the "Redeploy" button on your container app page.

## Setup

### 1. Fork or clone this repository

### 2. Configure GitHub Secrets

Go to your repo **Settings > Secrets and variables > Actions** and add:

| Secret | Required | Description |
|--------|----------|-------------|
| `GH_USERNAME` | Yes | Your GitHub username |
| `GH_PASSWORD` | Yes | Your GitHub password |
| `GH_2FA_SECRET` | No | Your GitHub TOTP 2FA secret (base32 string) for auto 2FA |
| `BACK4APP_URL` | Yes | Your Back4App container page URL. Must be the full URL pointing to the specific container, e.g. `https://containers.back4app.com/apps/xxxx-xxx-xxxxx` |
| `PAT_TOKEN` | Yes | GitHub Personal Access Token (classic). Required scopes: `repo`. Used by `gh` CLI to read/write the cooldown variable |

### 3. Run

- **Automatic**: Runs approximately every 5 minutes via cron schedule (GitHub Actions cron is best-effort; actual intervals may vary)
- **Manual**: Go to Actions tab > "Back4App Auto Redeploy" > "Run workflow"

## How It Works

1. Checks cooldown — skips if a successful redeploy happened within the last hour
2. Opens the Back4App container page with headless Chromium (Playwright)
3. Detects if login is required and clicks the GitHub OAuth button
4. Fills in GitHub credentials and handles 2FA if configured
5. After login, navigates to the target app page
6. Searches for a "Redeploy" button and clicks it if the container is stopped
7. Handles any confirmation dialog
8. Sets a 1-hour cooldown after a successful redeploy
9. Saves screenshots as GitHub Actions artifacts for debugging

## Screenshots

After each run, screenshots are uploaded as workflow artifacts (retained for 3 days). Check the Actions tab to download and review them.
