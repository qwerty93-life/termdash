# termdash

Self-hosted cyberpunk dashboard for a Termux tablet. Python stdlib only, no pip installs.

## setup (in Termux)

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

## console

The console panel shows, live:
- **server events** (`sys`): startup, config saves, errors
- **log files** you list in config under `"logs"`. It follows them like `tail -f`, e.g.
  `"logs": ["~/razor/bot.log", "~/xnvd/logs/latest.log"]`
  Not logging to a file yet? Run bots with `node bot.js >> ~/razor/bot.log 2>&1`, or with pm2: `"~/.pm2/logs/razor-out.log"`
- **command output**: type a command in the `$` box, or tap a quick-command button

Lines containing error/warn get colored automatically. Use the dropdown to filter by source.

Commands are **off by default**. They run with your Termux user's full permissions, so turning them on requires a token:

```sh
TERMDASH_TOKEN=pickSomethingLong python server.py
```

Then open `http://ip:8080/?t=pickSomethingLong`. Without the token, nobody can run anything, and the `$` box stays hidden. Commands get killed after 5 minutes, so don't run interactive stuff (`nano`, `top`); use `top -bn1` instead.

## config

Hit **⚙ config** in the dash. Edit weather coords, log files, quick commands and links. Saved to `data.json` next to the server, along with todos and notes, so they sync between every device that opens the dash.

With a token set, saving changes also needs `?t=TOKEN`. Others on the wifi can still *view* everything, console included, so don't tail logs that contain secrets.

## kiosk mode

Open it in Chrome on the tablet, then choose **⋮ → Add to Home screen**. Launch it from there and hit fullscreen. The page asks the browser to keep the screen on while it's open.
