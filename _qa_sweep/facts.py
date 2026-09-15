"""Ground truth pulled straight from sis_chatbot_db, for checking answers."""
import psycopg2, json, os, re

DSN = dict(host='127.0.0.1', port=5432, dbname='sis_chatbot_db',
           user='postgres', password='Mayur@2005')

def build():
    c = psycopg2.connect(**DSN); cur = c.cursor()
    def q(s, a=()):
        cur.execute(s, a); return cur.fetchall()
    f = {}
    f['status_split'] = dict(q("select current_status,count(*) from applications group by 1"))
    f['type_split'] = dict(q("select application_type,count(*) from applications group by 1"))
    f['channel_split'] = dict(q("select submission_channel,count(*) from applications group by 1"))
    f['stage_split'] = dict(q("select current_stage,count(*) from applications group by 1"))
    f['total_apps'] = sum(f['status_split'].values())
    f['officers'] = [dict(zip(('employee_id','name','email'), r))
                     for r in q("select employee_id,name,email from sis_officers order by 1")]
    rows = q("""select o.employee_id, w.ward_number, a.current_status, a.application_type,
                       a.submission_channel, count(*)
                from applications a join sis_officers o on a.assigned_officer_id=o.id
                join survey_numbers s on a.survey_number_id=s.id
                join blocks b on s.block_id=b.id join wards w on b.ward_id=w.id
                group by 1,2,3,4,5""")
    per = {}
    for emp, ward, st, ty, ch, n in rows:
        d = per.setdefault(emp, {'ward': ward, 'status': {}, 'type': {}, 'channel': {}, 'total': 0})
        d['status'][st] = d['status'].get(st, 0) + n
        d['type'][ty] = d['type'].get(ty, 0) + n
        d['channel'][ch] = d['channel'].get(ch, 0) + n
        d['total'] += n
    f['per_officer'] = per
    f['apps'] = [dict(zip(('application_number','application_type','current_status','current_stage',
                           'submission_date','submission_channel','can_number','igrs_form6_number',
                           'fee_amount','employee_id','ward_number','survey_no','patta_number',
                           'applicant_name','applicant_mobile','sale_deed_number','total_area_sqm',
                           'land_type','has_litigation','has_encroachment'), r))
                 for r in q("""select a.application_number,a.application_type,a.current_status,
                        a.current_stage,a.submission_date,a.submission_channel,a.can_number,
                        a.igrs_form6_number,a.fee_amount,o.employee_id,w.ward_number,s.survey_no,
                        s.patta_number,ap.name,ap.mobile,a.sale_deed_number,s.total_area_sqm,
                        s.land_type,s.has_litigation,s.has_encroachment
                     from applications a
                     left join sis_officers o on a.assigned_officer_id=o.id
                     left join applicants ap on a.applicant_id=ap.id
                     join survey_numbers s on a.survey_number_id=s.id
                     join blocks b on s.block_id=b.id join wards w on b.ward_id=w.id""")]
    f['surveys'] = [dict(zip(('survey_no','ward_number','patta_number','total_area_sqm','land_type',
                              'has_litigation','has_encroachment'), r))
                    for r in q("""select s.survey_no,w.ward_number,s.patta_number,s.total_area_sqm,
                          s.land_type,s.has_litigation,s.has_encroachment
                       from survey_numbers s join blocks b on s.block_id=b.id
                       join wards w on b.ward_id=w.id""")]
    f['fv_status'] = dict(q("select status,count(*) from field_visits group by 1"))
    f['fv_per_officer'] = {}
    for emp, st, n in q("""select o.employee_id, fv.status, count(*) from field_visits fv
              join sis_officers o on fv.officer_id=o.id group by 1,2"""):
        f['fv_per_officer'].setdefault(emp, {})[st] = n
    f['wards'] = [r[0] for r in q("select distinct ward_number from wards order by 1")]
    f['blocks'] = [r[0] for r in q("select distinct block_number from blocks order by 1")]
    f['decision_dates'] = dict(q("""select a.application_number, max(wh.performed_at)::date::text
            from applications a join workflow_history wh on wh.application_id=a.id
            where wh.to_stage in ('COMPLETED','REJECTED') group by 1"""))
    return f

if __name__ == '__main__':
    import sys
    f = build()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'facts.json')
    json.dump(f, open(out, 'w', encoding='utf-8'), ensure_ascii=False, default=str, indent=1)
    print('wrote', out, 'apps=', len(f['apps']), 'surveys=', len(f['surveys']))
