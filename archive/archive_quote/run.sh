#!/bin/bash
# /opt/quote/run.sh

cd /opt/quote || exit 1
log_file="panel.log"

user_site="/home/s00611272/.local/lib/python3.10/site-packages"
if [ -d "$user_site" ]; then
    export PYTHONPATH="$user_site:${PYTHONPATH:-}"
fi

python_cmd="/usr/bin/python3"
if command -v runuser >/dev/null 2>&1 && id -u s00611272 >/dev/null 2>&1; then
    python_cmd="runuser -u s00611272 -- /usr/bin/python3"
fi

run_app() {
    local script=$1
    while true; do
        $python_cmd "$script" >>"$log_file" 2>&1
        echo "$(date) $script crashed, restarting..." >>"$log_file"
        sleep 30
    done
}

run_app app.py &
run_app app_q.py &

wait -n
