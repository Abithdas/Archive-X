import sqlite3,random,datetime
random.seed(7)
c=sqlite3.connect('axdata/database/archive.db',timeout=30)
c.execute("DROP VIEW IF EXISTS archive_x_library_media")
c.execute("DROP VIEW IF EXISTS archive_x_published_media")
c.execute("CREATE VIEW archive_x_library_media AS SELECT m.*, 1 AS library_ready FROM media m")
c.execute("CREATE VIEW archive_x_published_media AS SELECT m.* FROM media m")
now=datetime.datetime.utcnow()
chs=[('-1001','Main Archive','main'),('-1002','Vertical Clips','vertical'),('-1003','A Very Long Channel Name That Should Truncate Nicely','main'),('-1004','Misc','main')]
for i,(tid,t,role) in enumerate(chs,1):
    c.execute("INSERT OR REPLACE INTO channels(id,telegram_id,title,username,role,updated_at) VALUES(?,?,?,?,?,?)",(i,tid,t,'ch%d'%i,role,now.isoformat()))
perf=['Ava Stone','Mia Rivera','Zoë Castellanos-Whitfield','Li','Priya Raman','Jordan Lee','Sofia Marchetti','Nina Volkov','Amélie Dubois','Chloe Park','Isabella Fernández de la Cruz','Ruby']
for i,n in enumerate(perf,1):
    c.execute("INSERT OR REPLACE INTO performers(id,name,normalized_name,gender,image_crop_version,profile,bio) VALUES(?,?,?,?,0,?,?)",(i,n,n.lower(),'female' if i%4 else 'male','Born 1995 · 168 cm','Short bio text. '*(i%5+1)))
studs=['Northlight','Aurora Films','Big Studio Productions International','Zed','Velvet House','Sunrise Media']
for i,n in enumerate(studs,1):
    c.execute("INSERT OR REPLACE INTO studios(id,name,normalized_name) VALUES(?,?,?)",(i,n,n.lower()))
tags=['4k','pov','outdoor','long-form','compilation','vertical','hd','interview']
for i,n in enumerate(tags,1):
    c.execute("INSERT OR REPLACE INTO tags(id,name,normalized_name) VALUES(?,?,?)",(i,n,n))
titles=['Short','A medium length title for a scene','An extremely long scene title that goes on and on to test how the cards truncate multi line text in narrow columns and wide ones alike','Scene 07','Évènement spécial — 完整版','S01E03 Finale (Uncut Director\'s Edition) [2160p]']
res=[(3840,2160,'4K'),(1920,1080,'1080p'),(1280,720,'720p'),(720,1280,'1080p'),(640,480,'480p')]
for i in range(1,73):
    w,h,rc=random.choice(res); ch=chs[i%4][0]
    d=(now-datetime.timedelta(days=i*3)).isoformat()
    c.execute("""INSERT OR REPLACE INTO media(id,source_channel,source_message_id,filename,title,size,duration,width,height,fps,codec,resolution_category,streamable,thumbnail_message_id,thumbnail_status,upload_date,favorite,status,created_at,updated_at,identification_state,orientation,site_name)
      VALUES(?,?,?,?,?,?,?,?,?,30,'h264',?,1,?, 'READY',?,?, 'ACTIVE',?,?, 'MATCHED',?,?)""",
      (i,ch,1000+i,f'file_{i}.mp4',random.choice(titles),random.randint(200_000_000,9_000_000_000),random.choice([95,640,1800,3725,7300]),w,h,rc,1000+i,d,1 if i%9==0 else 0,d,d,'portrait' if h>w else 'landscape',random.choice(studs)))
    for p in random.sample(range(1,13),random.randint(0,3)): c.execute("INSERT OR IGNORE INTO video_performers(video_id,performer_id,confidence) VALUES(?,?,1)",(i,p))
    for s in random.sample(range(1,7),random.randint(0,1)): c.execute("INSERT OR IGNORE INTO video_studios(video_id,studio_id,confidence) VALUES(?,?,1)",(i,s))
    for t in random.sample(range(1,9),random.randint(0,4)): c.execute("INSERT OR IGNORE INTO video_tags(video_id,tag_id,confidence) VALUES(?,?,1)",(i,t))
c.commit(); print('media',c.execute('select count(*) from media').fetchone())
