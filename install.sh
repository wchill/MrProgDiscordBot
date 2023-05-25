#!/usr/bin/env bash
USER="$(whoami)"

appendfile() {
  line=$1
  f=$2
  echo "Adding '$line' to $f" && sudo bash -c "(grep -qxF \"$line\" $f || echo \"$line\" >> $f)"
}

sudo apt update
sudo apt install -y git build-essential vim python3-pip

read -p "Set current hostname to: " -r HOSTNAME
read -p "AMQP/MQTT host: " -r HOST
read -p "AMQP/MQTT username: " -r USERNAME
read -p "AMQP/MQTT password: " -r -s PASSWORD
read -p "Discord bot token: " -r -s TOKEN

sudo hostnamectl set-hostname "$HOSTNAME"

# install tailscale
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up


if [[ ! -d "$HOME/venv" ]] ; then
  echo "Creating python virtualenv"
  python3 -m pip install virtualenv
  python3 -m virtualenv "$HOME/venv"
  git clone https://github.com/wchill/BattleNetworkData
  git clone https://github.com/wchill/MrProgUtils
  git clone https://github.com/wchill/MrProgDiscordBot
  "$HOME/venv/bin/python" -m pip install -e "$HOME/BattleNetworkData"
  "$HOME/venv/bin/python" -m pip install -e "$HOME/MrProgUtils"
  "$HOME/venv/bin/python" -m pip install -e "$HOME/MrProgDiscordBot"
fi


UNITFILE='/etc/systemd/system/discord-bot.service'
STARTUP_CMD="$HOME/venv/bin/python -u $HOME/MrProgDiscordBot/src/mrprog/bot/bot.py --host $HOST --username $USERNAME --password $PASSWORD --token $TOKEN 2&>1"
echo "Writing discord bot service file to $UNITFILE"
cat << EOF | sudo tee $UNITFILE > /dev/null
[Unit]
Description=Mr. Prog Discord Bot
After=systemd-networkd-wait-online.service
[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=~
ExecStart=$STARTUP_CMD
ExecStop=killall -u $USER $HOME/venv/bin/python
Restart=always
StandardOutput=journal
StandardError=inherit
[Install]
WantedBy=sysinit.target
EOF
systemctl daemon-reload
systemctl enable discord-bot

echo "Setup done, you might need to reboot"
