# -*- coding: utf-8 -*-
"""Run the bank through process_chat, append results to results.jsonl (resumable)."""
import asyncio, json, os, sys, time, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from driver import AsyncSessionLocal, officers, ask  # noqa


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='results.jsonl')
    ap.add_argument('--bank', default='bank.json')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--tags', default='')
    ap.add_argument('--fresh', action='store_true')
    args = ap.parse_args()

    bank = json.load(open(os.path.join(HERE, args.bank), encoding='utf-8'))
    if args.tags:
        want = set(args.tags.split(','))
        bank = [c for c in bank if want & set(c['tags'])]
    outp = os.path.join(HERE, args.out)
    done = set()
    if os.path.exists(outp) and not args.fresh:
        for line in open(outp, encoding='utf-8'):
            try:
                done.add(json.loads(line)['id'])
            except Exception:
                pass
    todo = [c for c in bank if c['id'] not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f'{len(todo)} to run ({len(done)} already done)', flush=True)

    fh = open(outp, 'a', encoding='utf-8')
    t0 = time.time()
    n = 0
    while todo:
        chunk, todo = todo[:40], todo[40:]
        async with AsyncSessionLocal() as db:
            offs = {o.employee_id: o for o in await officers(db)}
            for c in chunk:
                off = offs[c['emp']]
                r = await ask(c['q'], off, db, history=c['history'])
                rec = {'id': c['id'], 'q': c['q'], 'emp': c['emp'],
                       'tags': c['tags'], 'expect': c['expect'],
                       'history': c['history'],
                       'intent': r.get('intent'), 'ms': r.get('_ms'),
                       'action': r.get('action'),
                       'error': r.get('error'),
                       'response': (r.get('response') or '')[:6000],
                       'table_data': bool(r.get('table_data')),
                       'context_used': r.get('context_used')}
                fh.write(json.dumps(rec, ensure_ascii=False, default=str) + '\n')
                n += 1
                if n % 25 == 0:
                    el = time.time() - t0
                    print(f'  {n} done  {el:.0f}s  ({el/n:.2f}s/q)', flush=True)
                    fh.flush()
    fh.close()
    print('finished', n, 'in', int(time.time() - t0), 's')

asyncio.run(main())
