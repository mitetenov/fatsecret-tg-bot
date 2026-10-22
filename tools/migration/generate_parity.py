"""Run in fsbot:python-baseline, the pre-migration test image (never uses live APIs)."""
import asyncio
import dataclasses
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from fsbot.domain import barcodes, nutrition, servings
from fsbot.domain.daybounds import resolve
from fsbot.llm.parsing import parse_recognition, ParseError
from fsbot.bot.pipeline import apply_candidate

cases = []
def emit(op, data, expected):
    cases.append(dict(op=op, input=data, expected=expected))

for code in ['96385074','96385075','036000291452','4006381333931','00012345600012','01234565','5201340026780','123','abcdefgh','9780306406157','00 36000291452']:
    emit('gtin',dict(code=code),dict(valid=barcodes.valid_gtin(code),canonical=barcodes.canonical_gtin(code) or '',fatsecret=barcodes.fatsecret_gtin13(code) or ''))
for tz in ['Asia/Tbilisi','UTC','Europe/Berlin','America/New_York','Pacific/Apia']:
    for month,day in [(1,1),(3,29),(10,25),(12,31)]:
        for hour in range(24):
            now=datetime(2026,month,day,hour,30,tzinfo=timezone.utc)
            for hint in ['', 'yesterday']:
                date,meal=resolve(tz,now,date_hint=hint)
                emit('date',dict(tz=tz,now=now.isoformat(),hint=hint),dict(day=date.isoformat(),meal=meal.value))
rng=random.Random(42)
for _ in range(300):
    n=dict(kcal=rng.randint(-10,1010),protein=rng.randint(-1,101),fat=rng.randint(-1,101),carbs=rng.randint(-1,101))
    emit('nutrition',n,nutrition.plausible(**n))
for amount in [0,-1,0.001,1.5,100,450,'150','NaN',None]:
    for kind in ['text','label','plate','other']:
        raw=json.dumps(dict(kind=kind,items=[dict(query_en='oats',name_ru='Овсянка',amount=amount,unit='г')]))
        try:
            r=parse_recognition(raw)
            expected=dict(kind=r.kind,name=r.items[0].name_ru,amount=r.items[0].amount,unit=r.items[0].unit,confidence=r.items[0].confidence)
        except (ParseError,ValueError):
            expected=None
        emit('parse',dict(raw=raw),expected)
class FS:
    async def get_food(self,_):
        raise AssertionError('fixture should contain food')
async def pipeline():
    for metric_unit in ['g','ml',None]:
        for units in ['1','100','0','NaN']:
            food={'servings':{'serving':[dict(serving_id='0',metric_serving_amount=100,metric_serving_unit='g'),dict(serving_id='42',serving_description='100 '+(metric_unit or 'pieces'),metric_serving_amount='100' if metric_unit else None,metric_serving_unit=metric_unit,number_of_units=units,calories='120',protein='5',fat='4',carbohydrate='18')]}}
            for unit in ['g','ml','piece']:
                for amount in [1.5,100,450]:
                    item=dict(name_ru='Еда',amount=amount,unit=unit,confidence=.8,status='pending',candidates=[dict(food_id='9',title='Food',food=food)])
                    input=json.loads(json.dumps(item))
                    await apply_candidate(FS(),item,0)
                    keys=['food_id','serving_id','units','kcal','protein','fat','carbohydrate','needs_portion','portion']
                    expected={k:item.get(k) for k in keys}
                    emit('candidate',input,expected)
asyncio.run(pipeline())
Path('/out/python-parity.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2))
print(f'{len(cases)} frozen Python parity cases')

# Synthetic state created by the real pre-migration storage implementation.
from fsbot.storage import Storage
async def legacy_state():
    path=Path('/out/python-state.sqlite')
    path.unlink(missing_ok=True)
    store=Storage(path)
    await store.open()
    try:
        await store.ensure_user(42)
        await store.allow(42)
        await store.save_link(42,'synthetic-token','synthetic-secret')
        await store.set_tz(42,'Europe/Berlin')
        await store.bind_barcode(42,'0036000291452','77')
        draft=dict(day='2026-09-28',meal='lunch',kind='text',barcode=None,
            confidence=.8,needs_review=False,card_message_id=123,
            items=[dict(name_ru='Milk',query='milk',amount=150,unit='ml',status='written',
                        entry_id='old-entry',food_id='77',serving_id='771',units=150,
                        confidence=.8,error=None,candidates=[],chosen=0,mismatch=None),
                   dict(name_ru='Oats',query='oats',amount=60,unit='g',status='unknown',
                        entry_id=None,error=None,food_id='88',serving_id='881',units=60,
                        confidence=.8,candidates=[],chosen=0)])
        draft_id=await store.save_draft(42,draft)
        await store.record_write_success(draft_id,42,draft,'old-entry')
    finally:
        await store.close()
asyncio.run(legacy_state())
