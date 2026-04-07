#!/bin/bash
# WorkLens Installer for macOS
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo "${GREEN}WorkLens Installer${NC}"
echo "======================================"
echo ""

INSTALL_DIR="$HOME/WorkLens"
GITHUB_ZIP="https://github.com/azbakiyev/worklens/archive/refs/heads/main.zip"

# Check Python
if ! command -v python3 &>/dev/null; then
  echo "${RED}Python3 not found. Installing via Homebrew...${NC}"
  if ! command -v brew &>/dev/null; then
    echo "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  fi
  brew install python3
fi

PYTHON=$(which python3)
echo "${GREEN}Python found:${NC} $PYTHON"

# Download WorkLens
echo ""
echo "Downloading WorkLens..."
rm -rf /tmp/worklens_install
mkdir -p /tmp/worklens_install
curl -sL "$GITHUB_ZIP" -o /tmp/worklens_install/worklens.zip
unzip -q /tmp/worklens_install/worklens.zip -d /tmp/worklens_install/

# Install to ~/WorkLens
rm -rf "$INSTALL_DIR"
cp -r /tmp/worklens_install/worklens-main "$INSTALL_DIR"
rm -rf /tmp/worklens_install
echo "${GREEN}Installed to:${NC} $INSTALL_DIR"

# Create virtual environment
echo ""
echo "Installing dependencies..."
cd "$INSTALL_DIR"
"$PYTHON" -m venv .venv
.venv/bin/pip install -q -r requirements.txt
echo "${GREEN}Dependencies installed${NC}"

# Create launch script
cat > "$INSTALL_DIR/start_worklens.sh" << 'LAUNCH'
#!/bin/bash
cd "$HOME/WorkLens"
.venv/bin/python main.py &
sleep 3
open http://localhost:7771
LAUNCH
chmod +x "$INSTALL_DIR/start_worklens.sh"

# Create Desktop shortcut (macOS .command file)
cat > "$HOME/Desktop/WorkLens.command" << 'CMD'
#!/bin/bash
cd "$HOME/WorkLens"
.venv/bin/python main.py &
sleep 3
open http://localhost:7771
CMD
chmod +x "$HOME/Desktop/WorkLens.command"

# Create LaunchAgent for autostart
PLIST_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$PLIST_DIR"
cat > "$PLIST_DIR/ai.worklens.app.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>ai.worklens.app</string>
  <key>ProgramArguments</key>
  <array>
    <string>$HOME/WorkLens/.venv/bin/python</string>
    <string>$HOME/WorkLens/main.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
  <key>StandardOutPath</key><string>/tmp/worklens.log</string>
  <key>StandardErrorPath</key><string>/tmp/worklens.log</string>
</dict>
</plist>
PLIST

launchctl load "$PLIST_DIR/ai.worklens.app.plist" 2>/dev/null || true

echo ""
echo "${GREEN}WorkLens installed successfully!${NC}"
echo ""
echo "Opening WorkLens..."
sleep 2
cd "$INSTALL_DIR"
.venv/bin/python main.py &
sleep 4
open http://localhost:7771
echo ""
echo "WorkLens is running at http://localhost:7771"
echo "Desktop shortcut created: WorkLens.command"
echo ""
