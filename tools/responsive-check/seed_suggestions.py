import sqlite3,json,datetime
c=sqlite3.connect('axdata/database/archive.db',timeout=30)
now=datetime.datetime.now(datetime.timezone.utc).isoformat()
c.execute("DELETE FROM metadata_suggestions")
for i in range(1,13):
    payload={"title":f"Provider scene {i}","performers":[{"name":"Ava Stone"}],"studio":"Northlight","release_date":"2025-01-%02d"%i,"url":"https://example.com/s/%d"%i}
    c.execute("INSERT INTO metadata_suggestions(video_id,provider,provider_record_id,suggested_performer_name,confidence,score,reason,payload,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?, 'PENDING',?,?)",
      (i,'tpdb',f'r{i}','Ava Stone','HIGH',90-i,'Filename and duration match',json.dumps(payload),now,now))
    c.execute("UPDATE media SET identification_state='PARTIAL',metadata_source='needs_review' WHERE id=?",(i,))
c.commit(); print(c.execute("select count(*) from metadata_suggestions").fetchone())
