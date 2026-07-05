#!/bin/bash
# Filter connection limit test logs from tempest log file
# Usage: ./filter_connection_limit_logs.sh [logfile]

LOGFILE="${1:-tempest.log}"

if [ ! -f "$LOGFILE" ]; then
    echo "Error: Log file '$LOGFILE' not found"
    echo "Usage: $0 <path_to_tempest_log>"
    exit 1
fi

echo "=== Connection Limit Enforcement Test Logs ==="
grep -E "(Phase [0-9]+: Setting|Phase [0-9]+: Starting thread|Phase [0-9]+ results:|PASS: Phase|Phase [0-9]+ PASSED)" "$LOGFILE"
echo "==============================================="
