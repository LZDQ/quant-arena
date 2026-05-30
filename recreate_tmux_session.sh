#!/bin/sh

# Usage: ./recreate_tmux_session.sh [--no-attach]

# tmux windows:
#   0 bash                -> bash
#   1 config              -> cd ~/.quant-arena
#   2 deploy-backend      -> python -m quant_arena

set -eu

SESSION_NAME="quant-arena"
ATTACH=1

if [ "${1-}" = "--no-attach" ]; then
	ATTACH=0
fi

if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
	# Create detached session with window 0.
	tmux new-session -d -s "$SESSION_NAME" -n bash

	# Create remaining windows.
	tmux new-window -d -t "${SESSION_NAME}:1" -n config -c ~/.quant-arena
	tmux new-window -d -t "${SESSION_NAME}:2" -n deploy-backend

	# Send commands.
	tmux send-keys -t "${SESSION_NAME}:2" "source .venv/bin/activate" C-m
	tmux send-keys -t "${SESSION_NAME}:2" "python -m quant_arena" C-m

	tmux select-window -t "${SESSION_NAME}:0"
fi

if [ "$ATTACH" -eq 1 ]; then
	exec tmux attach-session -t "$SESSION_NAME"
fi

exit 0
