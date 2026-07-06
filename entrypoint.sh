#!/bin/bash

# SPDX-FileCopyrightText: 2026 Arcangelo Massari <arcangelo.massari@unibo.it>
#
# SPDX-License-Identifier: ISC

set -e

start_virtuoso() {
    echo "Starting embedded Virtuoso..."
    
    export DBA_PASSWORD=${DBA_PASSWORD:-dba}
    export DAV_PASSWORD=${DAV_PASSWORD:-dba}
    export VIRTUOSO_BULK_DIR=${VIRTUOSO_BULK_DIR:-/opt/virtuoso-data}
    export VIRTUOSO_DATA_DIR=${VIRTUOSO_DATA_DIR:-/opt/virtuoso-data}
    
    mkdir -p "$VIRTUOSO_DATA_DIR"
    
    cat > "$VIRTUOSO_DATA_DIR/virtuoso.ini" << VEOF
[Database]
DatabaseFile = virtuoso.db
ErrorLogFile = virtuoso.log
LockFile = virtuoso.lck
TransactionFile = virtuoso.trx
xa_persistent_file = virtuoso.pxa
ErrorLogLevel = 7
FileExtend = 200
MaxCheckpointRemap = 2000
Striping = 0
TempStorage = TempDatabase

[TempDatabase]
DatabaseFile = virtuoso-temp.db
TransactionFile = virtuoso-temp.trx
MaxCheckpointRemap = 2000
Striping = 0

[Parameters]
ServerPort = 1111
LiteMode = 0
DisableUnixSocket = 1
DisableTcpSocket = 0
MaxClientConnections = 10
CheckpointInterval = 60
O_DIRECT = 0
CaseMode = 2
MaxStaticCursorRows = 5000
CheckpointAuditTrail = 0
AllowOSCalls = 0
SchedulerInterval = 10
DirsAllowed = /usr/share/proj,../vad,.,/opt/virtuoso-data
ThreadCleanupInterval = 1
ThreadThreshold = 10
ResourcesCleanupInterval = 1
FreeTextBatchSize = 100000
SingleCPU = 0
VADInstallDir = /opt/virtuoso-opensource/share/virtuoso/vad/
PrefixResultNames = 0
RdfFreeTextRulesSize = 100
IndexTreeMaps = 64
MaxMemPoolSize = 200000000
MacSpotlight = 0
MaxQueryMem = ${VIRTUOSO_MAX_QUERY_MEM:-16G}
VectorSize = 1000
MaxVectorSize = 1000000
AdjustVectorSize = 0
ThreadsPerQuery = ${VIRTUOSO_THREADS_PER_QUERY:-4}
AsyncQueueMaxThreads = ${VIRTUOSO_ASYNC_QUEUE_MAX_THREADS:-10}
NumberOfBuffers = ${VIRTUOSO_NUMBER_OF_BUFFERS:-10000}
MaxDirtyBuffers = ${VIRTUOSO_MAX_DIRTY_BUFFERS:-7500}

[HTTPServer]
ServerPort = 8890
ServerRoot = /opt/virtuoso-opensource/share/virtuoso/vsp
MaxClientConnections = 10
DavRoot = DAV
EnabledDavVSP = 0
HTTPProxyEnabled = 0
TempASPXDir = 0
DefaultMailServer = localhost:25
MaxKeepAlives = 10
KeepAliveTimeout = 10
MaxCachedProxyConnections = 10
ProxyConnectionCacheTimeout = 15
HTTPThreadSize = 280000
HttpPrintWarningsInOutput = 0
Charset = UTF-8
MaintenancePage = atomic.html
EnabledGzipContent = 1

[AutoRepair]
BadParentLinks = 0

[Client]
SQL_PREFETCH_ROWS = 100
SQL_PREFETCH_BYTES = 16000
SQL_QUERY_TIMEOUT = 0
SQL_TXN_TIMEOUT = 0

[VDB]
ArrayOptimization = 0
NumArrayParameters = 10
VDBDisconnectTimeout = 1000
KeepConnectionOnFixedThread = 0

[Replication]
ServerName = db-KGI
ServerEnable = 1
QueueMax = 50000

[Zero Config]
ServerName = virtuoso (KGI)

[URIQA]
DynamicLocal = 0
DefaultHost = localhost:8890

[SPARQL]
MaxConstructTriples = 10000
;MaxQueryCostEstimationTime = 400
;MaxQueryExecutionTime = 60
DefaultQuery = SELECT (COUNT(*) AS ?triples) WHERE {?s ?p ?o}
DeferInferenceRulesInit = 0
MaxMemInUse = 0

[Plugins]
LoadPath = /opt/virtuoso-opensource/lib/virtuoso/hosting
VEOF
    
    cd "$VIRTUOSO_DATA_DIR"
    /opt/virtuoso-opensource/bin/virtuoso-t +foreground +wait +configfile "$VIRTUOSO_DATA_DIR/virtuoso.ini" &
    
    echo "Waiting for Virtuoso to start..."
    for i in {1..30}; do
        if wget -q --spider http://localhost:8890/sparql 2>/dev/null; then
            echo "Virtuoso is ready!"
            break
        fi
        if [ $i -eq 30 ]; then
            echo "Timeout waiting for Virtuoso to start"
            exit 1
        fi
        sleep 2
    done
    
    echo "Configuring SPARQL permissions..."
    /opt/virtuoso-opensource/bin/isql 1111 dba dba << 'EOF'
DB.DBA.RDF_DEFAULT_USER_PERMS_SET('nobody', 7);
DB.DBA.USER_GRANT_ROLE('SPARQL', 'SPARQL_UPDATE');
EXIT;
EOF
    echo "SPARQL permissions configured successfully"
    
}

shutdown() {
    echo "Shutting down services..."
    if command -v pkill >/dev/null 2>&1; then
        pkill -f virtuoso-t || true
        pkill -f qlever-server || true
        pkill -f python || true
    fi
    exit 0
}

benchmark_sparql_backend() {
    local backend="virtuoso"
    local next_is_backend=0

    for arg in "$@"; do
        if [ "$next_is_backend" = "1" ]; then
            backend="$arg"
            next_is_backend=0
            continue
        fi

        case "$arg" in
            "--sparql-backend")
                next_is_backend=1
                ;;
            "--sparql-backend="*)
                backend="${arg#*=}"
                ;;
        esac
    done

    printf '%s\n' "$backend"
}

trap shutdown SIGTERM SIGINT

case "${1:-app}" in
    "virtuoso-only")
        start_virtuoso
        wait
        ;;
    "benchmark")
        if [ "$(benchmark_sparql_backend "${@:2}")" = "virtuoso" ]; then
            start_virtuoso
        fi
        echo "Starting benchmark..."
        cd /app
        exec uv run python benchmarks/run_krown_benchmark.py "${@:2}"
        ;;
    "app"|*)
        # Check if EMBEDDED_VIRTUOSO is enabled
        if [ "${EMBEDDED_VIRTUOSO:-true}" = "true" ]; then
            start_virtuoso
        fi
        echo "Starting main application..."
        cd /app
        exec uv run python app.py
        ;;
esac
