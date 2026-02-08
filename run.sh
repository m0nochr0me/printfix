#!/bin/bash

exec python -m app &
wait -n

exit $?