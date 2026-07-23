#!/bin/bash
# File: /root/install.sh

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}YouTube Downloader Bot - Automatic Installation${NC}"
echo "======================================"

# Get configuration from user
read -p "Please enter your Telegram Bot Token: " BOT_TOKEN
read -p "Please enter your API_ID: " API_ID
read -p "Please enter your API_HASH: " API_HASH
read -p "Please enter your SESSION_STRING: " SESSION_STRING
read -p "Please enter your TARGET_CHANNEL: " TARGET_CHANNEL
read -p "Please enter your TARGET_CHANNEL_USERNAME: " TARGET_CHANNEL_USERNAME
read -p "Please enter your DOWNLOAD_DIR: " DOWNLOAD_DIR
read -p "Please enter your COOKIES_FILE: " COOKIES_FILE 

# Install prerequisites
echo -e "${YELLOW}Installing prerequisites...${NC}"
apt update -y
apt install -y python3 python3-pip python3-venv ffmpeg aria2 wget curl git

# Clone or create project
cd /root
rm -rf youtube_bot
mkdir -p youtube_bot
cd youtube_bot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install libraries
pip install --upgrade pip
pip install python-telegram-bot==20.7 telethon==1.36.0 yt-dlp==2024.11.18 Pillow==10.3.0 requests==2.32.3 python-dotenv==1.0.1 aiohttp==3.9.5

# Create .env file
cat > .env << EOF
TELEGRAM_TOKEN=$BOT_TOKEN
API_ID=$API_ID
API_HASH=$API_HASH
SESSION_STRING=$SESSION_STRING
TARGET_CHANNEL=$TARGET_CHANNEL
TARGET_CHANNEL_USERNAME=$TARGET_CHANNEL_USERNAME
DOWNLOAD_DIR=$DOWNLOAD_DIR
COOKIES_FILE=$COOKIES_FILE
EOF

# Download the main bot file
curl -o bot.py https://raw.githubusercontent.com/amith-ti/TeleTube/main/TeleTube.py

# Create cookies file
# touch /root/cookies.txt

# Configure systemd service
cat > /etc/systemd/system/youtube-bot.service << 'EOF'
[Unit]
Description=YouTube Download Bot
After=network.target

[Service]
User=root
WorkingDirectory=/root/youtube_bot
Environment="PATH=/root/youtube_bot/venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/root/youtube_bot/venv/bin/python /root/youtube_bot/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable youtube-bot.service
systemctl start youtube-bot.service

echo -e "${GREEN}Installation completed successfully!${NC}"
echo "Service status:"
systemctl status youtube-bot.service --no-pager
