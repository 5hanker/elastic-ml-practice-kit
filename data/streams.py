"""Data stream names."""

TRACES = "traces-apm-mlws"
ERRORS = "logs-apm.error-mlws"
MT = "metrics-apm.transaction.1m-mlws"
MST = "metrics-apm.service_transaction.1m-mlws"
MSUM = "metrics-apm.service_summary.1m-mlws"
LOGS = "logs-mlws.app-default"
ALL_STREAMS = [TRACES, ERRORS, MT, MST, MSUM, LOGS]
APM_STREAMS = [TRACES, ERRORS, MT, MST, MSUM]
