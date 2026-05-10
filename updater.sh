#!/bin/bash
sleep 2
pkill -9 python
cd ~/farm
git fetch origin
git reset --hard origin/main
git clean -fd
python main.py
