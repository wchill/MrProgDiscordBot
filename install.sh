#!/usr/bin/env bash
DIR="$( dirname -- "${BASH_SOURCE[0]}"; )";   # Get the directory name
DIR="$( realpath -e -- "$DIR"; )";    # Resolve its full path if need be

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


if [[ ! -d "$DIR/venv" ]] ; then
  echo "Creating python virtualenv"
  python3 -m pip install virtualenv
  python3 -m virtualenv "$DIR/venv"
  git clone https://github.com/wchill/BattleNetworkData
  git clone https://github.com/wchill/MrProgUtils
  git clone https://github.com/wchill/MrProgDiscordBot
  "$DIR/venv/bin/python" -m pip install -e "$DIR/BattleNetworkData"
  "$DIR/venv/bin/python" -m pip install -e "$DIR/MrProgUtils"
  "$DIR/venv/bin/python" -m pip install -e "$DIR/MrProgDiscordBot"
fi


UNITFILE='/etc/systemd/system/discord-bot.service'
STARTUP_CMD="$DIR/venv/bin/python $DIR/MrProgDiscordBot/mrprog/bot/bot.py --host $HOST --username $USERNAME --password $PASSWORD --token $TOKEN"
echo "Writing discord bot service file to $UNITFILE"
cat << EOF | sudo tee $UNITFILE > /dev/null
[Unit]
Description=Mr. Prog Discord Bot
After=systemd-networkd-wait-online.service
Wants=systemd-networkd-wait-online.service
[Service]
Type=simple
ExecStart=$STARTUP_CMD
Restart=always
StandardOutput=journal+console
[Install]
WantedBy=sysinit.target
EOF
systemctl daemon-reload
systemctl enable trade-worker

echo "Setup done, you might need to reboot"
