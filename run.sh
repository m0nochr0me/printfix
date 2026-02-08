#!/bin/bash

# Start taskiq worker in background
taskiq worker app.worker.broker:broker \
    --fs-discover \
    --tasks-pattern "app/**/tasks.py" \
    --workers "${PFX_WORKER_CONCURRENCY:-2}" &

# Start FastAPI server in background
exec python -m app &

# Wait for either process to exit
wait -n
exit $?
