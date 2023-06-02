#!/usr/bin/env bash

updaterepo() {
  repo=$1
  cd "$HOME/$repo" || exit
  git reset --hard HEAD
  git clean -fd
  git pull
  "$HOME/venv/bin/python" -m pip install -e "$HOME/$repo"
}

sudo systemctl stop discord-bot

updaterepo "BattleNetworkData"
updaterepo "MrProgUtils"
updaterepo "MrProgDiscordBot"

sudo systemctl start discord-bot
