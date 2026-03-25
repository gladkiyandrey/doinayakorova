#!/bin/zsh
set -euo pipefail
cd '/Users/andrey/.freelance_ua_notifier_runtime'
exec '/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9' '/Users/andrey/.freelance_ua_notifier_runtime/freelance_ua_notifier.py' --config '/Users/andrey/.freelance_ua_notifier_runtime/config.json'
