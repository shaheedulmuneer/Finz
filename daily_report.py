"""Generate daily reports for all workspaces.
Run at 08:00 Dubai time using cron or Windows Task Scheduler.
"""
import main
with main.db() as conn:
    workspaces=[r['id'] for r in conn.execute('SELECT id FROM workspaces')]
for wid in workspaces:
    report=main.create_report(wid)
    print(f'Workspace {wid} • {report["day"]} • {len(report["recs"])} recommendations')
