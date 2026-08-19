#!/usr/bin/env python3
"""Thin entry so `python3 wechat-watch.py send --peer X --text Y` matches regions."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "wechat-watch-regions")
os.execv(sys.executable, [sys.executable, SCRIPT, *sys.argv[1:]])
