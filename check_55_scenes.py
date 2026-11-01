import sqlite3, time

conn = sqlite3.connect('data/session.db')
c = conn.cursor()
c.execute("SELECT id, stage, status, error FROM jobs WHERE id = 55")
print('Job 55:', c.fetchone())
c.execute("SELECT count(*) FROM scenes WHERE job_id = 55")
print('Scenes count:', c.fetchone()[0])
c.execute("SELECT count(*) FROM flow_tasks WHERE job_id = 55")
print('Flow tasks count:', c.fetchone()[0])
conn.close()
