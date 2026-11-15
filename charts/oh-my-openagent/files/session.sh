#!/bin/bash
# The tmux session: OmO in the work directory, a login shell when it exits
. "$HOME/.omo-path.sh"
cd "$HOME/$OMO_WORKDIR"
omo || echo "omo exited with status $?"
echo "Type omo to start OmO again."
exec bash -l
