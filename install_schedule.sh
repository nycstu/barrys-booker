#!/bin/bash
# Install launchd plist to run the booker every Thursday at 11:59a ET

set -e
cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"

PLIST_NAME="com.stuartwall.barrys-booker"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PROJECT_DIR}/venv/bin/python3</string>
        <string>${PROJECT_DIR}/book_barrys.py</string>
        <string>--wait</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>4</integer>
        <key>Hour</key>
        <integer>11</integer>
        <key>Minute</key>
        <integer>59</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>${PROJECT_DIR}/logs/launchd_stdout.log</string>

    <key>StandardErrorPath</key>
    <string>${PROJECT_DIR}/logs/launchd_stderr.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>TZ</key>
        <string>America/New_York</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
EOF

# Load it
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo "Schedule installed!"
echo "  Plist: $PLIST_PATH"
echo "  Runs: Every Thursday at 11:59a ET (waits until 12:00:00 to book)"
echo ""
echo "To check status:  launchctl list | grep barrys"
echo "To uninstall:      launchctl unload $PLIST_PATH && rm $PLIST_PATH"
echo ""
