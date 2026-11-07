"""Recent non-success rows in the usage DB.   python scripts\\usage_report.py"""
import datetime
import sqlite3
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "data/company_ai.db"
conn = sqlite3.connect(path)

print("by client/status:")
for row in conn.execute(
    "SELECT client, endpoint, status, COUNT(*) FROM usage_log "
    "GROUP BY client, endpoint, status ORDER BY client, endpoint, status"
):
    print("  ", row)

print("\nlast 15 non-success:")
for ts, client, endpoint, status, rid in conn.execute(
    "SELECT ts, client, endpoint, status, request_id FROM usage_log "
    "WHERE status != 'success' ORDER BY ts DESC LIMIT 15"
):
    when = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    print(f"  {when}  {client:10} {endpoint:12} {status:10} {rid}")
