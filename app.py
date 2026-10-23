from flask import Flask, render_template, request, jsonify, Response
from pathlib import Path
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import sqlite3, json, uuid, os, re, hmac
from functools import wraps

BASE=Path(__file__).parent
DB=BASE/'racecontrol.sqlite3'
app=Flask(__name__)
DATABASE_URL=os.environ.get('DATABASE_URL','')
APP_USERNAME=os.environ.get('APP_USERNAME','')
APP_PASSWORD=os.environ.get('APP_PASSWORD','')
if os.environ.get('RENDER') and (not DATABASE_URL or not APP_USERNAME or not APP_PASSWORD):
    raise RuntimeError('Configure DATABASE_URL, APP_USERNAME y APP_PASSWORD antes de publicar.')

@app.before_request
def require_auth():
    if request.path == '/healthz':
        return None
    if not APP_USERNAME or not APP_PASSWORD:
        return None if not os.environ.get('RENDER') else ('Autenticación no configurada', 503)
    auth=request.authorization
    if not auth or not (hmac.compare_digest(auth.username or '',APP_USERNAME) and hmac.compare_digest(auth.password or '',APP_PASSWORD)):
        return Response('Acceso restringido a RaceControl',401,{'WWW-Authenticate':'Basic realm="RaceControl"'})

@app.get('/healthz')
def healthz(): return jsonify(status='ok')

ANCHOR=date(2026,11,2)
TYPES={'Natación Máster','Natación hijo','Running','Triatlón','Conciertos','Otro'}
STATES={'Me interesa','Pendiente','Inscrito','Pagado','Confirmado','Cancelado','Completado'}

class PGConnection:
    def __init__(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn=psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10)
    def __enter__(self): return self
    def __exit__(self,*args):
        if args[0]: self.conn.rollback()
        else: self.conn.commit()
        self.conn.close()
    def execute(self, query, params=None):
        if isinstance(params,dict):
            query=re.sub(r':([a-zA-Z_][a-zA-Z0-9_]*)',lambda m:'%('+m.group(1)+')s',query)
        else:
            query=query.replace('?','%s')
        return self.conn.execute(query,params)

def connection():
    if DATABASE_URL: return PGConnection()
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    return c

def init():
    with connection() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL, participant TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL, start_time TEXT, end_time TEXT, city TEXT, status TEXT NOT NULL, notes TEXT, created_at TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS exceptions (day TEXT PRIMARY KEY, work INTEGER NOT NULL)''')
init()

def work_on(d, exceptions=None):
    if exceptions is not None and d.isoformat() in exceptions:
        return bool(exceptions[d.isoformat()])
    return ((d-ANCHOR).days % 21) < 7

def exceptions_dict():
    with connection() as c:
        return {r['day']:r['work'] for r in c.execute('SELECT day,work FROM exceptions')}

def parse_event(data, original=None):
    title=str(data.get('title','')).strip()
    if not title: raise ValueError('Escribe el nombre del evento.')
    category=data.get('category','')
    if category not in TYPES: raise ValueError('Categoría no válida.')
    participant=data.get('participant','')
    if participant not in ('Yo','Mi hijo','Familia'): raise ValueError('Participante no válido.')
    status=data.get('status','Me interesa')
    if status not in STATES: raise ValueError('Estado no válido.')
    start=date.fromisoformat(data['start_date'])
    end=date.fromisoformat(data.get('end_date') or data['start_date'])
    if end<start: raise ValueError('La fecha final es anterior a la inicial.')
    if (end-start).days>60: raise ValueError('El evento no puede superar 61 días.')
    t1=data.get('start_time') or None
    t2=data.get('end_time') or None
    for t in (t1,t2):
        if t: datetime.strptime(t,'%H:%M')
    if t1 and t2 and start==end and t2<=t1: raise ValueError('La hora final debe ser posterior a la inicial.')
    return dict(id=original or str(uuid.uuid4()),title=title[:160],category=category,participant=participant,
       start_date=start.isoformat(),end_date=end.isoformat(),start_time=t1,end_time=t2,city=str(data.get('city') or '')[:160],
       status=status,notes=str(data.get('notes') or '')[:2000],created_at=datetime.now(ZoneInfo('America/Santiago')).isoformat())

def overlap_days(a,b):
    start=max(date.fromisoformat(a['start_date']),date.fromisoformat(b['start_date']))
    end=min(date.fromisoformat(a['end_date']),date.fromisoformat(b['end_date']))
    return (start,end) if start<=end else None

def time_conflict(a,b):
    d=overlap_days(a,b)
    if not d: return None
    if a.get('start_time') and a.get('end_time') and b.get('start_time') and b.get('end_time') and a['start_date']==a['end_date']==b['start_date']==b['end_date']:
        return 'critical' if a['start_time'] < b['end_time'] and b['start_time'] < a['end_time'] else None
    return 'potential'

def detect(ev, records, ex):
    if ev['status']=='Cancelado':return []
    results=[]
    for other in records:
        if other['id']==ev['id'] or other['status']=='Cancelado':continue
        level=time_conflict(ev,other)
        if level:
            results.append(dict(level=level,reason=('Horarios superpuestos' if level=='critical' else 'Coincidencia de fecha; faltan horarios completos'),title=other['title'],type='event',id=other['id']))
    start=date.fromisoformat(ev['start_date']);end=date.fromisoformat(ev['end_date'])
    if any(work_on(start+timedelta(days=i),ex) for i in range((end-start).days+1)):
        results.append(dict(level='potential',reason='Coincide con al menos un día de turno laboral',title='Turno laboral',type='work',id='work'))
    return results

@app.get('/')
def index():return render_template('index.html')

@app.get('/api/events')
def events():
    with connection() as c: return jsonify([dict(r) for r in c.execute('SELECT * FROM events ORDER BY start_date,start_time')])

@app.post('/api/events')
def add():
    try: record=parse_event(request.get_json(force=True))
    except (ValueError,KeyError,TypeError) as e:return jsonify(error=str(e)),400
    with connection() as c:
        c.execute('''INSERT INTO events (id,title,category,participant,start_date,end_date,start_time,end_time,city,status,notes,created_at) VALUES (:id,:title,:category,:participant,:start_date,:end_date,:start_time,:end_time,:city,:status,:notes,:created_at)''',record)
    return jsonify(record),201

@app.put('/api/events/<event_id>')
def edit(event_id):
    with connection() as c:
        prev=c.execute('SELECT id FROM events WHERE id=?',(event_id,)).fetchone()
        if not prev:return jsonify(error='Evento no encontrado.'),404
        try: record=parse_event(request.get_json(force=True),event_id)
        except (ValueError,KeyError,TypeError) as e:return jsonify(error=str(e)),400
        c.execute('''UPDATE events SET title=:title,category=:category,participant=:participant,start_date=:start_date,end_date=:end_date,start_time=:start_time,end_time=:end_time,city=:city,status=:status,notes=:notes WHERE id=:id''',record)
        return jsonify(record)

@app.delete('/api/events/<event_id>')
def delete(event_id):
    with connection() as c:c.execute('DELETE FROM events WHERE id=?',(event_id,))
    return jsonify(ok=True)

@app.post('/api/check')
def check():
    try: ev=parse_event(request.get_json(force=True),request.get_json().get('id'))
    except (ValueError,KeyError,TypeError) as e:return jsonify(error=str(e)),400
    with connection() as c: records=[dict(r) for r in c.execute('SELECT * FROM events')]
    return jsonify(conflicts=detect(ev,records,exceptions_dict()))

@app.get('/api/work')
def work():
    try:
        start=date.fromisoformat(request.args.get('start',date.today().isoformat()))
        end=date.fromisoformat(request.args.get('end',(date.today()+timedelta(days=60)).isoformat()))
        if (end-start).days>1100 or end<start:raise ValueError()
    except ValueError:return jsonify(error='Rango inválido'),400
    ex=exceptions_dict()
    return jsonify([{'date':(start+timedelta(days=i)).isoformat(),'work':work_on(start+timedelta(days=i),ex)} for i in range((end-start).days+1)])

@app.post('/api/work/exception')
def exception():
    data=request.get_json(force=True)
    try: day=date.fromisoformat(data['date']).isoformat()
    except (KeyError, ValueError):return jsonify(error='Fecha inválida'),400
    override=data.get('work')
    with connection() as c:
        if override is None:c.execute('DELETE FROM exceptions WHERE day=?',(day,))
        elif isinstance(override,bool):c.execute('INSERT INTO exceptions (day,work) VALUES (?,?) ON CONFLICT(day) DO UPDATE SET work=excluded.work',(day,int(override)))
        else:return jsonify(error='Valor inválido'),400
    return jsonify(ok=True)

@app.get('/calendar.ics')
def ics():
    def esc(s):return str(s or '').replace('\\','\\\\').replace(';','\\;').replace(',','\\,').replace('\n','\\n')
    lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//RaceControl//ES','CALSCALE:GREGORIAN','X-WR-CALNAME:RaceControl']
    with connection() as c: records=[dict(r) for r in c.execute('SELECT * FROM events')]
    for e in records:
        lines+=['BEGIN:VEVENT','UID:'+e['id']+'@racecontrol','DTSTAMP:'+datetime.utcnow().strftime('%Y%m%dT%H%M%SZ'),
                'DTSTART;VALUE=DATE:'+e['start_date'].replace('-',''),
                'DTEND;VALUE=DATE:'+(date.fromisoformat(e['end_date'])+timedelta(days=1)).strftime('%Y%m%d'),
                'SUMMARY:'+esc(e['title']+' · '+e['participant']), 'LOCATION:'+esc(e['city']),
                'DESCRIPTION:'+esc(e['category']+' | '+e['status']+' | '+e['notes']), 'END:VEVENT']
    ex=exceptions_dict()
    day=ANCHOR
    while day<=date(2028,12,31):
        if work_on(day,ex):
            lines+=['BEGIN:VEVENT','UID:shift-'+day.isoformat()+'@racecontrol','DTSTAMP:'+datetime.utcnow().strftime('%Y%m%dT%H%M%SZ'),
                'DTSTART;VALUE=DATE:'+day.strftime('%Y%m%d'),'DTEND;VALUE=DATE:'+(day+timedelta(days=1)).strftime('%Y%m%d'),
                'SUMMARY:Turno laboral','END:VEVENT']
        day+=timedelta(days=1)
    lines.append('END:VCALENDAR')
    return Response('\r\n'.join(lines)+'\r\n',mimetype='text/calendar',headers={'Content-Disposition':'attachment;filename=racecontrol.ics'})

if __name__=='__main__':app.run(debug=not bool(os.environ.get('RENDER')),host='127.0.0.1',port=int(os.environ.get('PORT','5000')))
