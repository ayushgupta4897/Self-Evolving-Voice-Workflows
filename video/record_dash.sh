#!/bin/zsh
# Record a real Chrome window driving the live dashboard.
# Chrome's own tab bar + URL bar stay in frame so it is obviously a real browser.
set -e
OUT="$1"           # output .mov
DUR="$2"           # seconds
shift 2
URLS=("$@")        # urls to visit, one per beat

osascript <<'EOF'
tell application "Google Chrome"
  activate
  if (count of windows) = 0 then make new window
  set bounds of front window to {8, 26, 1504, 952}
  set URL of active tab of front window to "http://localhost:3100/#diff"
end tell
EOF
sleep 4

# region is in points; capture the Chrome window incl. its chrome
screencapture -v -V "$DUR" -R 8,26,1496,926 "$OUT" &
REC=$!
sleep 1.2

for u in "${URLS[@]}"; do
  osascript -e "tell application \"Google Chrome\" to set URL of active tab of front window to \"$u\"" >/dev/null
  sleep 2.6
done

wait $REC
echo "recorded $OUT"
