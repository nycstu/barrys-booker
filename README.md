# Barry's Booker

Automatically books your Barry's Bootcamp class the moment the booking window opens. Runs on [Render](https://render.com) as a scheduled cron job - no machine needs to be on.

Built with Claude Code.

---

## What it does

1. Wakes up 10 minutes before the booking window opens
2. Logs into barrys.com using your credentials
3. Navigates to your target class (studio, day, time)
4. Books your preferred spot the moment it becomes available

## Requirements

- Barry's Bootcamp account
- [Render](https://render.com) account (free tier works)
- Docker (for local testing)

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/barrys-booker.git
cd barrys-booker
```

### 2. Configure your environment

Create a `.env` file (never commit this):

```bash
BARRYS_EMAIL=your@email.com
BARRYS_PASSWORD=your_password
BARRYS_STUDIO=noho          # studio slug from the Barry's URL
BARRYS_CLASS_TIME=07:20     # 24h format
BARRYS_DAY=thursday         # day of week to book
BARRYS_SPOTS=DF-33,DF-32,DF-30   # preferred spots in priority order
```

### 3. Test locally

```bash
docker build -t barrys-booker .
docker run --env-file .env barrys-booker
```

### 4. Deploy to Render

1. Push the repo to GitHub
2. Go to [render.com](https://render.com) and create a new **Cron Job**
3. Connect your GitHub repo
4. Render will detect `render.yaml` and configure automatically
5. Add your environment variables in the Render dashboard (Settings > Environment)

The cron schedule in `render.yaml` is set for Thursday at 11:50am ET (bookings open at noon). Adjust for your class day/time.

## Customizing the schedule

Edit `render.yaml`:

```yaml
schedule: "50 15 * * 4"  # 15:50 UTC = 11:50 ET, Thursday (day 4)
```

Use [crontab.guru](https://crontab.guru) to build your schedule. Set it to fire 10 minutes before the booking window opens.

## Finding your studio slug and spot IDs

1. Go to [barrys.com](https://www.barrys.com) and navigate to your studio's schedule
2. The studio slug is in the URL (e.g., `barrys.com/studios/noho/schedule`)
3. Spot IDs (like `DF-33`) are shown on the studio map when booking manually

## File structure

```
barrys-booker/
- book_barrys.py      Main booking script (Playwright)
- render.yaml         Render cron job config
- Dockerfile          Docker container definition
- requirements.txt    Python dependencies
- setup.sh            Local setup helper
- install_schedule.sh Local cron setup (alternative to Render)
```

## Notes

- `.env` and `auth_state.json` are gitignored - never commit credentials
- Playwright runs headless inside Docker
- If Barry's updates their site structure, the selectors in `book_barrys.py` may need updating
