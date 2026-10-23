"""Synthetic 10-team × 1,279-QA × 3-calls monitor benchmark.

Run: python tests/perf_monitor.py
No private dataset, network access or real model calls are needed.
"""
import json
import os
import sys
sys.path.insert(0,str(__import__('pathlib').Path(__file__).resolve().parent.parent))
import statistics
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from app.db import db, dumps, init_db
from app.services.monitor import ensure
from app.services import competition
import app.db as database

TEAM_COUNT=10
QUESTION_COUNT=1279
CALLS_PER_QUESTION=3
HISTORICAL_BATCHES=3
LIMIT_MS=float(os.environ.get('WARGAME_MONITOR_MAX_MS','250'))

def bench():
    with tempfile.TemporaryDirectory() as temp:
        original=database.HOME
        database.HOME=Path(temp)
        try:
            init_db()
            dims=['L2_main']*179+['L3_main']*61+['L1_sub']*757+['L2_sub']*282
            questions=[dict(question_id=f'q{i}',level=d.split('_')[0],role=d.split('_')[1])
                       for i,d in enumerate(dims)]
            call_body=dumps({'status':'ok','output':'X'*1500,'usage':{
                'prompt_tokens':100,'completion_tokens':30,'total_tokens':130}})
            plan={'official':True,'calls':3,'mode':'majority_vote'}
            batch_ids=[]
            with db() as c:
                for batch_index in range(HISTORICAL_BATCHES):
                    bid=uuid4().hex;batch_ids.append(bid)
                    created=f'2026-10-23T00:00:{batch_index:02d}+00:00'
                    c.execute("""INSERT INTO evaluation_batches(id,request_key,name,created_at,body,started)
                                 VALUES(?,?,?,?,?,1)""",
                              (bid,uuid4().hex,f'批次{batch_index}',created,dumps({'plan':plan,'request':{}})))
                    for team in range(TEAM_COUNT):
                        rid=uuid4().hex;body=dict(name=f'队伍{team}',kind='official',
                            model={'name':'model','model_id':'M','provider':'openai','is_aggregator':False},
                            mode='majority_vote',calls=3,early_stop=False,
                            prompt={'version':'v1'},official=True,
                            config={'competition':{'max_calls_per_question':3,'timeout_per_question_seconds':120}},
                            cohort='same',dataset_sha256='digest',questions=questions,batch_id=bid,
                            team_id=f'team-{team}',submission_version='v1',
                            started_at='2026-10-23T00:00:00+00:00',
                            finished_at='2026-10-23T00:10:00+00:00')
                        c.execute('INSERT INTO runs(id,status,created_at,body) VALUES(?,?,?,?)',
                                  (rid,'completed',created,dumps(body)))
                        ensure(c,rid,body)
                        c.execute('INSERT INTO batch_entries VALUES(?,?,?,?)',
                                  (bid,f'team-{team}',rid,rid))
                        c.executemany('INSERT INTO results VALUES(?,?,?)',(
                            (rid,f'q{i}',dumps({'status':'ok','correct':i%2==0,'dimension':dim}))
                            for i,dim in enumerate(dims)))
                        c.executemany('INSERT INTO calls VALUES(?,?,?,?)',(
                            (rid,f'q{i}',n,call_body)
                            for i in range(QUESTION_COUNT) for n in range(1,CALLS_PER_QUESTION+1)))
            # Warm SQLite's page cache, then measure repeated API-equivalent service paths.
            for _ in range(2):
                competition.get_batch(batch_ids[0])
                competition.list_batches()
            times_batch=[];times_list=[]
            for _ in range(5):
                started=time.perf_counter();v=competition.get_batch(batch_ids[0])
                times_batch.append((time.perf_counter()-started)*1000)
                assert v['teams']==TEAM_COUNT and v['completed']==TEAM_COUNT*QUESTION_COUNT
                started=time.perf_counter();b=competition.list_batches()
                times_list.append((time.perf_counter()-started)*1000)
                assert len(b)==HISTORICAL_BATCHES and sum(x['actual_calls'] for x in b)==(
                    HISTORICAL_BATCHES*TEAM_COUNT*QUESTION_COUNT*CALLS_PER_QUESTION)
            size=(Path(temp)/'wargame.sqlite').stat().st_size/1024/1024
            print(f'DB={size:.1f} MiB; get_batch median={statistics.median(times_batch):.2f} ms, '
                  f'list_batches(3) median={statistics.median(times_list):.2f} ms; '
                  f'limit={LIMIT_MS:.0f} ms',flush=True)
            assert statistics.median(times_batch)<LIMIT_MS, times_batch
            assert statistics.median(times_list)<LIMIT_MS, times_list
        finally:
            database.HOME=original

if __name__=='__main__':
    bench()
