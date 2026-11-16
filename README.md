# termdash

Self-hosted cyberpunk dashboard for a Termux tablet. Python stdlib only, no pip installs.

## setup (in Termux)

Straight from GitHub:
```sh
pkg install git python termux-api
git clone https://github.com/YOURNAME/termdash ~/termdash
cd ~/termdash && python server.py
```
Update later with `git pull` (there's also a quick-command button for it), then restart the server.

Or manually:

```sh
pkg update && pkg install python termux-api
# also install the "Termux:API" app from F-Droid (same source as your Termux) for battery stats
termux-setup-storage          # lets it read /sdcard usage
mkdir -p ~/termdash && cd ~/termdash
# put server.py + index.html here
python server.py              # -> http://localhost:8080
```

From any device on the same wifi, open `http://<tablet-ip>:8080` (it prints the IP on start, or run `ifconfig`).

## keep it alive

```sh
termux-wake-lock              # stops android from killing it
# run in background:
nohup python ~/termdash/server.py > ~/termdash/log.txt 2>&1 &
```

Autostart on boot: install **Termux:Boot** (F-Droid), open it once, then:

```sh
mkdir -p ~/.termux/boot
cat > ~/.termux/boot/termdash.sh <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
cd ~/termdash && python server.py > log.txt 2>&1
EOF
chmod +x ~/.termux/boot/termdash.sh
```

## apps (hosting your bots, sites, scripts)

termdash has a built-in process manager, so you don't need pm2. Hit **+ add app** and fill in:

| field | example |
|---|---|
| name | `razor` |
| folder | `~/razor` |
| command | `node index.js` / `npm start` / `python bot.py` / `python -m http.server 3000` |
| build step | optional, e.g. `npm run build` |
| url | optional, for sites: adds a ↗ open button |

Then you get **▶ start / ■ stop / ↻ restart / ⇣ deploy / logs** per app, plus CPU, RAM, uptime and restart count.

- **start with dashboard**: the app starts automatically whenever termdash starts (so with Termux:Boot your bots come back after a reboot)
- **restart if it crashes**: waits 2s, 4s, 8s… (max 60s) between restarts, so a broken bot doesn't spam restarts. The ↻ column counts the restarts.
- **deploy**: runs `git pull`, then `npm install` (if there's a package.json), `pip install -r requirements.txt` (if there's one) and your build step, then restarts. If any step fails, it doesn't restart, and the old version keeps running.
- **logs**: filters the console to that app. Everything is also saved to `termdash/logs/<app>.log` (rotates at 2MB).

**Getting a bot onto the tablet:**
```sh
pkg install nodejs git          # or python
git clone https://github.com/YOU/razor ~/razor
cd ~/razor && npm install
nano .env                       # put DISCORD_TOKEN=... here
```
Then add it in the dash with folder `~/razor`, command `node index.js`.

**Secrets go in a `.env` file in the app's folder**, never in the dash config: anyone on your wifi can read the config. Your bot loads `.env` itself (`require('dotenv').config()` / `python-dotenv`).

**Apps run *inside* termdash.** Stop the server (Ctrl+C, or closing the Termux session) and your apps stop too. If Termux gets killed hard, the leftover bot gets killed the next time termdash starts, so it never runs twice. Keep `termux-wake-lock` on, or Android will kill everything when the screen goes off.

Hosting a site? Run it on a port (e.g. 3000) and open `http://<tablet-ip>:3000` from any device on your wifi. Ports below 1024 don't work without root.

## console

A real command line in the dashboard. Type in the `$` box at the bottom of the console:
- `cd` sticks between commands, and the prompt shows where you are
- ↑/↓ scroll through your history (saved in the browser)
- **^C** button (or Ctrl+C on an empty line) stops whatever's running; Ctrl+L clears the screen
- don't run interactive programs (`nano`, `top`, `htop`): there's no keyboard input, so they'll just hang. Use `top -bn1` instead

It also follows log files live. List them in config under `"logs"`, e.g.
`"logs": ["~/razor/bot.log", "~/.pm2/logs/xnvd-out.log"]`
Lines with error/warn get colored, and the dropdown filters by source.

**Who can type commands:**
- **on the tablet itself** (localhost): always
- **from another device on the wifi**: only if you start the server with `TERMDASH_TOKEN=somethingLong python server.py` and open `http://ip:8080/?t=somethingLong`. Otherwise the `$` box is greyed out, and so are the kill buttons.

Anyone on the wifi can still *view* the console, so don't tail logs that contain bot tokens.

## processes

Shows what's running under Termux (your bots, pm2, the dashboard itself), sorted by CPU, with RAM use and age. ✕ kills a process; it asks "sure?" first, so tap it twice.
Android only lets Termux see its own processes, so other apps won't show up here.

## uptime checks

Every 60s the tablet makes a request to each URL under `"checks"` and graphs the response time (red bars = down). When a check goes up or down, it also shows in the console.
`"checks": [{"name": "Netheriums", "url": "https://netheriums.com"}]`

## config

Hit **⚙ config** in the dash. Edit weather coords, log files, uptime checks, quick commands and links. It's saved to `data.json` next to the server, so every device that opens the dash gets the same setup.

With a token set, other devices also need `?t=TOKEN` to save config changes.

## kiosk mode

Open it in Chrome on the tablet, then choose **⋮ → Add to Home screen**. Launch it from there and hit fullscreen. The page asks the browser to keep the screen on while it's open.
