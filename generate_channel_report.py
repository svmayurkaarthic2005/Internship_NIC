"""
Generate channel_report.html — rich interactive page showing SIS applications
by submission channel (CSC / SRO / Citizen) with NISD pending vs completed
prominently featured.
"""
import psycopg2, urllib.parse, json, decimal, datetime

password = urllib.parse.unquote("Mayur%402005")
conn = psycopg2.connect(host="127.0.0.1", port=5432, dbname="sis_chatbot_db",
                        user="postgres", password=password)
cur = conn.cursor()

cur.execute("""
    SELECT
        a.application_number,
        a.application_type,
        a.current_status,
        a.submission_channel,
        a.submission_date,
        a.can_number,
        ap.name               AS applicant_name,
        ap.mobile             AS mobile_number,
        ap.gender,
        ap.occupation,
        a.fee_amount,
        a.igrs_form6_number,
        a.submission_source_name,
        a.submission_camp_flag,
        w.ward_number,
        sn.survey_no,
        sn.patta_number,
        sn.total_area_sqm,
        a.current_stage,
        a.declared_reason,
        a.challan_number,
        a.payment_mode
    FROM applications a
    LEFT JOIN applicants ap ON a.applicant_id = ap.id
    LEFT JOIN survey_numbers sn ON a.survey_number_id = sn.id
    LEFT JOIN blocks b ON sn.block_id = b.id
    LEFT JOIN wards w ON b.ward_id = w.id
    ORDER BY a.application_type, a.submission_channel, a.current_status, a.application_number
""")

def default(o):
    if isinstance(o, decimal.Decimal): return float(o)
    if isinstance(o, (datetime.date, datetime.datetime)): return str(o)
    return str(o)

cols = [d[0] for d in cur.description]
rows = [dict(zip(cols, r)) for r in cur.fetchall()]
cur.close()
conn.close()

data_json  = json.dumps(rows,  default=default)

html = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>SIS Channel Report — NISD Pending & Completed</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet"/>
<style>
/* ──────────── RESET & TOKENS ──────────── */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0d1117;
  --bg2:#161b22;
  --bg3:#21262d;
  --bg4:#2d333b;
  --border:#30363d;
  --border2:#444c56;
  --text:#e6edf3;
  --muted:#8b949e;
  --accent:#58a6ff;
  --green:#3fb950;
  --yellow:#d29922;
  --red:#f85149;
  --blue:#79c0ff;
  --purple:#bc8cff;

  --csc:#1f6feb;
  --csc-glow:rgba(31,111,235,.25);
  --csc-bg:rgba(31,111,235,.10);
  --csc-border:rgba(31,111,235,.30);

  --sro:#388bfd;
  --sro-glow:rgba(56,139,253,.22);
  --sro-bg:rgba(56,139,253,.09);
  --sro-border:rgba(56,139,253,.28);

  --citizen:#3fb950;
  --citizen-glow:rgba(63,185,80,.20);
  --citizen-bg:rgba(63,185,80,.09);
  --citizen-border:rgba(63,185,80,.28);

  --nisd:#58a6ff;
  --nisd-bg:rgba(88,166,255,.10);
  --nisd-border:rgba(88,166,255,.28);

  --isd:#e3b341;
  --isd-bg:rgba(227,179,65,.10);
  --isd-border:rgba(227,179,65,.28);

  --approved:#3fb950;
  --pending:#d29922;
  --inprog:#58a6ff;
  --rejected:#f85149;

  --r:8px; --rl:12px; --rxl:16px;
  --trans:.16s ease;
  font-family:'Inter',system-ui,sans-serif;
  font-size:14px;
  color:var(--text);
  background:var(--bg);
  line-height:1.5;
}

html{scroll-behavior:smooth}
body{min-height:100vh}

/* ──────────── HEADER ──────────── */
.header{
  background:linear-gradient(180deg,#0d1117 0%,#161b22 100%);
  border-bottom:1px solid var(--border);
  padding:20px 32px 16px;
  position:sticky;top:0;z-index:200;
  backdrop-filter:blur(12px);
}
.header-inner{max-width:1700px;margin:0 auto;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.hdr-logo{
  width:40px;height:40px;border-radius:10px;flex-shrink:0;
  background:linear-gradient(135deg,#1f6feb 0%,#388bfd 100%);
  display:flex;align-items:center;justify-content:center;font-size:18px;
  box-shadow:0 0 18px rgba(31,111,235,.45);
}
.hdr-title{font-size:18px;font-weight:800;letter-spacing:-.4px}
.hdr-sub{font-size:11px;color:var(--muted);margin-top:1px}
.hdr-spacer{flex:1}
.hdr-pill{
  background:var(--bg3);border:1px solid var(--border);border-radius:6px;
  padding:5px 12px;font-size:12px;font-family:'JetBrains Mono',monospace;color:var(--muted);
}
.nav-tabs{display:flex;gap:4px}
.nav-tab{
  padding:6px 14px;border-radius:6px;border:1px solid transparent;
  background:none;color:var(--muted);font-size:12px;font-weight:600;
  cursor:pointer;font-family:inherit;transition:all var(--trans);
  text-decoration:none;
}
.nav-tab:hover{background:var(--bg3);color:var(--text);border-color:var(--border)}
.nav-tab.active{background:var(--bg3);color:var(--text);border-color:var(--border2)}

/* ──────────── MAIN ──────────── */
.main{max-width:1700px;margin:0 auto;padding:24px 32px 80px}

/* ──────────── HERO STATS ──────────── */
.hero{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));
  gap:14px;margin-bottom:28px;
}
.hero-card{
  border-radius:var(--rl);border:1px solid var(--border);
  background:var(--bg2);padding:20px 22px;
  position:relative;overflow:hidden;
  transition:transform var(--trans),box-shadow var(--trans);
}
.hero-card::after{
  content:'';position:absolute;top:-40px;right:-40px;
  width:120px;height:120px;border-radius:50%;opacity:.06;
}
.hero-card.nisd-hero::after{background:var(--nisd)}
.hero-card.isd-hero::after{background:var(--isd)}
.hero-card.all-hero::after{background:var(--green)}
.hero-card:hover{transform:translateY(-2px);box-shadow:0 8px 28px rgba(0,0,0,.4)}
.hero-top{display:flex;align-items:flex-start;gap:12px;margin-bottom:14px}
.hero-icon{font-size:26px}
.hero-label{font-size:13px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
.hero-num{font-size:36px;font-weight:800;font-family:'JetBrains Mono',monospace;line-height:1.1;margin-top:2px}
.hero-num.nisd-color{color:var(--nisd)}
.hero-num.isd-color{color:var(--isd)}
.hero-num.all-color{color:var(--green)}
.hero-chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px}
.hchip{
  padding:3px 9px;border-radius:5px;font-size:11px;font-weight:600;
  display:inline-flex;align-items:center;gap:4px;
}
.hchip-approved{background:rgba(63,185,80,.13);color:var(--approved);border:1px solid rgba(63,185,80,.28)}
.hchip-pending{background:rgba(210,153,34,.13);color:var(--pending);border:1px solid rgba(210,153,34,.28)}
.hchip-in_progress{background:rgba(88,166,255,.12);color:var(--inprog);border:1px solid rgba(88,166,255,.28)}
.hchip-rejected{background:rgba(248,81,73,.11);color:var(--rejected);border:1px solid rgba(248,81,73,.28)}
.hchip-csc{background:var(--csc-bg);color:#79c0ff;border:1px solid var(--csc-border)}
.hchip-sro{background:var(--sro-bg);color:#a5d6ff;border:1px solid var(--sro-border)}
.hchip-citizen{background:var(--citizen-bg);color:var(--citizen);border:1px solid var(--citizen-border)}

/* ──────────── NISD SPOTLIGHT ──────────── */
.nisd-spotlight{
  margin-bottom:28px;border-radius:var(--rxl);
  border:1px solid var(--nisd-border);
  background:linear-gradient(135deg,rgba(88,166,255,.05) 0%,rgba(88,166,255,.02) 100%);
  overflow:hidden;
}
.nisd-spotlight-header{
  padding:18px 24px;background:var(--nisd-bg);
  border-bottom:1px solid var(--nisd-border);
  display:flex;align-items:center;gap:12px;flex-wrap:wrap;
}
.nisd-spotlight-title{font-size:16px;font-weight:800;color:var(--nisd)}
.nisd-spotlight-sub{font-size:12px;color:var(--muted);margin-left:auto}

.nisd-status-grid{
  display:grid;grid-template-columns:1fr 1fr;
  border-bottom:1px solid var(--border);
}
@media(max-width:768px){.nisd-status-grid{grid-template-columns:1fr}}
.nisd-half{padding:20px 24px;border-right:1px solid var(--border)}
.nisd-half:last-child{border-right:none}
.nisd-half-title{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:14px;display:flex;align-items:center;gap:8px}
.nisd-half-title.pending-title{color:var(--pending)}
.nisd-half-title.completed-title{color:var(--approved)}
.nisd-channel-row{
  display:flex;align-items:center;gap:10px;padding:10px 0;
  border-bottom:1px solid var(--border);
}
.nisd-channel-row:last-child{border-bottom:none}
.nisd-ch-icon{font-size:16px}
.nisd-ch-label{font-size:13px;font-weight:600;flex:1}
.nisd-ch-bars{flex:2;display:flex;flex-direction:column;gap:4px}
.nisd-bar-row{display:flex;align-items:center;gap:6px;font-size:11px}
.nisd-bar-track{flex:1;height:5px;background:var(--bg3);border-radius:99px;overflow:hidden;min-width:60px}
.nisd-bar-fill{height:100%;border-radius:99px}
.nisd-bar-val{width:24px;text-align:right;font-family:'JetBrains Mono',monospace;font-weight:600;font-size:11px}
.nisd-ch-total{font-size:20px;font-weight:800;font-family:'JetBrains Mono',monospace;color:var(--nisd);min-width:36px;text-align:right}

/* ──────────── CHANNEL CARDS ──────────── */
.channel-cards{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));
  gap:14px;margin-bottom:24px;
}
.ch-card{
  border-radius:var(--rl);border:1px solid var(--border);
  background:var(--bg2);overflow:hidden;
  transition:transform var(--trans),box-shadow var(--trans);
  cursor:pointer;
}
.ch-card:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.35)}
.ch-card.ch-active{box-shadow:0 0 0 1.5px var(--accent)}
.ch-card-bar{height:3px;background:var(--border)}
.ch-card.csc .ch-card-bar{background:linear-gradient(90deg,var(--csc),#388bfd)}
.ch-card.sro .ch-card-bar{background:linear-gradient(90deg,#388bfd,#79c0ff)}
.ch-card.citizen .ch-card-bar{background:linear-gradient(90deg,var(--citizen),#56d364)}
.ch-card-body{padding:18px 20px}
.ch-card-top{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.ch-icon{font-size:22px}
.ch-name{font-size:14px;font-weight:700}
.ch-desc{font-size:11px;color:var(--muted);margin-top:1px}
.ch-total{
  margin-left:auto;font-size:28px;font-weight:800;
  font-family:'JetBrains Mono',monospace;
}
.csc .ch-total{color:var(--csc)}
.sro .ch-total{color:#388bfd}
.citizen .ch-total{color:var(--citizen)}
.ch-type-grid{display:flex;flex-direction:column;gap:6px}
.ch-type-row{display:flex;align-items:center;gap:8px}
.ch-type-label{font-size:11px;font-weight:700;width:40px;color:var(--muted)}
.ch-track{flex:1;height:8px;background:var(--bg3);border-radius:99px;overflow:hidden;position:relative}
.ch-fill{height:100%;border-radius:99px;transition:width .5s cubic-bezier(.4,0,.2,1)}
.ch-fill-nisd{background:linear-gradient(90deg,#1f6feb,#58a6ff)}
.ch-fill-isd{background:linear-gradient(90deg,#9a6700,#e3b341)}
.ch-type-count{font-size:12px;font-weight:700;font-family:'JetBrains Mono',monospace;width:28px;text-align:right}
.ch-status-pills{display:flex;flex-wrap:wrap;gap:4px;margin-top:12px;padding-top:12px;border-top:1px solid var(--border)}

/* ──────────── FILTER BAR ──────────── */
.filter-bar{
  background:var(--bg2);border:1px solid var(--border);border-radius:var(--rl);
  padding:14px 18px;display:flex;flex-wrap:wrap;gap:10px;align-items:center;
  margin-bottom:20px;position:sticky;top:61px;z-index:100;
}
.fg{display:flex;gap:5px;flex-wrap:wrap;align-items:center}
.fl{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-right:2px}
.fb{
  padding:4px 11px;border-radius:6px;border:1px solid var(--border);
  background:var(--bg3);color:var(--muted);font-size:12px;font-weight:600;
  cursor:pointer;transition:all var(--trans);font-family:inherit;
}
.fb:hover{border-color:var(--border2);color:var(--text)}
.fb.on{background:var(--accent);color:#fff;border-color:var(--accent);box-shadow:0 0 10px rgba(88,166,255,.25)}
.fb.ch-CSC.on{background:var(--csc);border-color:var(--csc);box-shadow:0 0 10px var(--csc-glow)}
.fb.ch-sub_registrar.on{background:#388bfd;border-color:#388bfd;box-shadow:0 0 10px var(--sro-glow)}
.fb.ch-citizen.on{background:var(--citizen);border-color:var(--citizen);box-shadow:0 0 10px var(--citizen-glow)}
.fb.tp-NISD.on{background:var(--nisd);border-color:var(--nisd)}
.fb.tp-ISD.on{background:var(--isd);border-color:var(--isd);color:#000}
.fb.st-approved.on{background:var(--approved);border-color:var(--approved)}
.fb.st-pending.on{background:var(--pending);border-color:var(--pending);color:#000}
.fb.st-in_progress.on{background:var(--inprog);border-color:var(--inprog)}
.fb.st-rejected.on{background:var(--rejected);border-color:var(--rejected)}
.fsep{width:1px;height:20px;background:var(--border);flex-shrink:0}
.fsearch{flex:1;min-width:180px;position:relative}
.fsearch input{
  width:100%;background:var(--bg3);border:1px solid var(--border);border-radius:6px;
  padding:6px 10px 6px 30px;color:var(--text);font-size:13px;
  font-family:inherit;outline:none;transition:border-color var(--trans);
}
.fsearch input:focus{border-color:var(--accent)}
.fsearch-icon{position:absolute;left:9px;top:50%;transform:translateY(-50%);color:var(--muted)}
.fcount{font-size:12px;color:var(--muted);white-space:nowrap}

/* ──────────── TABS (type view) ──────────── */
.type-tabs{display:flex;gap:6px;margin-bottom:16px;flex-wrap:wrap}
.tt{
  padding:7px 18px;border-radius:8px;border:1px solid var(--border);
  background:var(--bg2);color:var(--muted);font-size:13px;font-weight:600;
  cursor:pointer;font-family:inherit;transition:all var(--trans);
}
.tt:hover{border-color:var(--border2);color:var(--text)}
.tt.tt-on.nisd{background:var(--nisd-bg);color:var(--nisd);border-color:var(--nisd-border)}
.tt.tt-on.isd{background:var(--isd-bg);color:var(--isd);border-color:var(--isd-border)}
.tt.tt-on.all{background:var(--bg3);color:var(--text);border-color:var(--border2)}

/* ──────────── SECTIONS ──────────── */
.section-wrap{margin-bottom:8px}
.section-hdr{
  padding:13px 18px;display:flex;align-items:center;gap:10px;
  border-radius:var(--r) var(--r) 0 0;border:1px solid;border-bottom:none;
}
.section-hdr.csc{background:var(--csc-bg);border-color:var(--csc-border)}
.section-hdr.sro{background:var(--sro-bg);border-color:var(--sro-border)}
.section-hdr.citizen{background:var(--citizen-bg);border-color:var(--citizen-border)}
.sh-icon{font-size:18px}
.sh-label{font-size:13px;font-weight:700;flex:1}
.sh-badge{
  font-size:11px;font-weight:700;padding:3px 10px;border-radius:99px;
  font-family:'JetBrains Mono',monospace;
}
.csc .sh-badge{background:var(--csc-bg);color:#79c0ff;border:1px solid var(--csc-border)}
.sro .sh-badge{background:var(--sro-bg);color:#a5d6ff;border:1px solid var(--sro-border)}
.citizen .sh-badge{background:var(--citizen-bg);color:var(--citizen);border:1px solid var(--citizen-border)}

/* ──────────── STATUS SUB-SECTION ──────────── */
.status-sub{border:1px solid var(--border);border-top:none;margin-top:0}
.status-sub:last-child{border-radius:0 0 var(--rl) var(--rl)}
.status-sub-hdr{
  padding:8px 18px;display:flex;align-items:center;gap:8px;
  background:var(--bg3);border-bottom:1px solid var(--border);
}
.ssh-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.dot-pending{background:var(--pending)}
.dot-approved{background:var(--approved)}
.dot-in_progress{background:var(--inprog)}
.dot-rejected{background:var(--rejected)}
.ssh-label{font-size:11px;font-weight:700;flex:1;text-transform:uppercase;letter-spacing:.5px}
.ssh-label.pending{color:var(--pending)}
.ssh-label.approved{color:var(--approved)}
.ssh-label.in_progress{color:var(--inprog)}
.ssh-label.rejected{color:var(--rejected)}
.ssh-count{font-size:11px;font-weight:700;font-family:'JetBrains Mono',monospace;color:var(--muted)}

/* ──────────── TABLE ──────────── */
.tbl-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12.5px;min-width:900px}
thead th{
  background:var(--bg3);padding:9px 12px;text-align:left;
  font-size:10.5px;font-weight:700;color:var(--muted);text-transform:uppercase;
  letter-spacing:.4px;white-space:nowrap;border-bottom:1px solid var(--border);
}
tbody tr{border-bottom:1px solid rgba(48,54,61,.7);transition:background var(--trans)}
tbody tr:last-child{border-bottom:none}
tbody tr:hover{background:rgba(255,255,255,.025)}
tbody td{padding:9px 12px;vertical-align:middle}

.app-link{
  font-family:'JetBrains Mono',monospace;font-size:11.5px;font-weight:500;
  color:var(--accent);cursor:pointer;white-space:nowrap;
}
.app-link:hover{text-decoration:underline}

/* ──────────── BADGE ──────────── */
.badge{display:inline-flex;align-items:center;gap:3px;padding:2px 7px;border-radius:99px;font-size:11px;font-weight:700;white-space:nowrap}
.b-ISD{background:var(--isd-bg);color:var(--isd);border:1px solid var(--isd-border)}
.b-NISD{background:var(--nisd-bg);color:var(--nisd);border:1px solid var(--nisd-border)}
.b-MERGE{background:rgba(188,140,255,.1);color:var(--purple);border:1px solid rgba(188,140,255,.28)}
.b-approved{background:rgba(63,185,80,.11);color:var(--approved);border:1px solid rgba(63,185,80,.28)}
.b-pending{background:rgba(210,153,34,.12);color:var(--pending);border:1px solid rgba(210,153,34,.28)}
.b-in_progress{background:rgba(88,166,255,.11);color:var(--inprog);border:1px solid rgba(88,166,255,.28)}
.b-rejected{background:rgba(248,81,73,.10);color:var(--rejected);border:1px solid rgba(248,81,73,.28)}
.b-csc{background:var(--csc-bg);color:#79c0ff;border:1px solid var(--csc-border)}
.b-sro{background:var(--sro-bg);color:#a5d6ff;border:1px solid var(--sro-border)}
.b-citizen{background:var(--citizen-bg);color:var(--citizen);border:1px solid var(--citizen-border)}

.mono{font-family:'JetBrains Mono',monospace;font-size:11px}
.muted{color:var(--muted)}
.fee{color:#56d364;font-family:'JetBrains Mono',monospace;font-size:12px}
.ward-c{display:inline-block;padding:1px 7px;border-radius:4px;background:var(--bg3);border:1px solid var(--border);font-size:11px;font-family:'JetBrains Mono',monospace;color:var(--muted)}
.sno-c{display:inline-block;padding:1px 7px;border-radius:4px;background:var(--nisd-bg);border:1px solid rgba(88,166,255,.15);font-size:11px;font-family:'JetBrains Mono',monospace;color:var(--nisd)}
.igrs-c{display:inline-block;padding:1px 7px;border-radius:4px;background:rgba(188,140,255,.09);border:1px solid rgba(188,140,255,.2);font-size:11px;font-family:'JetBrains Mono',monospace;color:var(--purple)}
.stage-c{font-size:10.5px;color:var(--muted);font-family:'JetBrains Mono',monospace}

/* ──────────── COMBINATION MATRIX ──────────── */
.combo-section{margin-top:36px}
.combo-title{font-size:15px;font-weight:800;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.combo-tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:16px}
.ctab{
  padding:5px 14px;border-radius:6px;border:1px solid var(--border);
  background:var(--bg3);color:var(--muted);font-size:12px;font-weight:600;
  cursor:pointer;font-family:inherit;transition:all var(--trans);
}
.ctab:hover{color:var(--text);border-color:var(--border2)}
.ctab.ctab-on{background:var(--accent);color:#fff;border-color:var(--accent)}
.combo-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px}
.combo-card{
  background:var(--bg2);border:1px solid var(--border);border-radius:var(--rl);
  padding:14px 16px;cursor:pointer;
  transition:transform var(--trans),box-shadow var(--trans),border-color var(--trans);
  position:relative;overflow:hidden;
}
.combo-card:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(0,0,0,.4);border-color:var(--border2)}
.combo-card-label{font-size:12px;font-weight:700;color:var(--muted);margin-bottom:6px;display:flex;align-items:center;gap:6px}
.combo-card-count{font-size:26px;font-weight:800;font-family:'JetBrains Mono',monospace;color:var(--accent);margin-bottom:8px;line-height:1}
.combo-chips{display:flex;flex-wrap:wrap;gap:4px}
.cc{padding:2px 7px;border-radius:4px;font-size:10.5px;font-weight:600;background:var(--bg3);border:1px solid var(--border);color:var(--muted)}
.cc.approved{background:rgba(63,185,80,.1);color:var(--approved);border-color:rgba(63,185,80,.25)}
.cc.pending{background:rgba(210,153,34,.1);color:var(--pending);border-color:rgba(210,153,34,.25)}
.cc.in_progress{background:rgba(88,166,255,.1);color:var(--inprog);border-color:rgba(88,166,255,.25)}
.cc.rejected{background:rgba(248,81,73,.09);color:var(--rejected);border-color:rgba(248,81,73,.25)}
.cc.completed{background:rgba(63,185,80,.1);color:var(--approved);border-color:rgba(63,185,80,.25)}

.combo-card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px}
.combo-card.nisd-combo::before{background:linear-gradient(90deg,var(--nisd),#79c0ff)}
.combo-card.isd-combo::before{background:linear-gradient(90deg,#9a6700,var(--isd))}
.combo-card.multi-combo::before{background:linear-gradient(90deg,var(--csc),var(--citizen))}

/* ──────────── DRAWER ──────────── */
.overlay{
  position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:500;
  display:none;align-items:flex-end;backdrop-filter:blur(6px);
}
.overlay.open{display:flex}
.drawer{
  background:var(--bg2);border:1px solid var(--border);
  border-radius:var(--rxl) var(--rxl) 0 0;width:100%;
  max-height:85vh;overflow-y:auto;padding:24px 28px;
  animation:slideUp .22s ease;
}
@keyframes slideUp{from{transform:translateY(100%)}to{transform:translateY(0)}}
.drawer-handle{width:40px;height:4px;border-radius:99px;background:var(--border2);margin:0 auto 18px}
.drawer-hdr{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:18px}
.drawer-hdr-title{font-size:15px;font-weight:800;font-family:'JetBrains Mono',monospace;color:var(--accent)}
.drawer-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px}
.df{background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:12px}
.df-label{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px}
.df-val{font-size:13px;color:var(--text);word-break:break-all}
.drawer-close{margin-top:18px;padding:9px 22px;border-radius:8px;background:var(--bg3);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:13px;cursor:pointer;transition:all var(--trans)}
.drawer-close:hover{background:var(--rejected);border-color:var(--rejected)}

/* ──────────── UTIL ──────────── */
.empty-state{text-align:center;padding:40px 24px;color:var(--muted);background:var(--bg2);border:1px solid var(--border);border-radius:var(--rl);margin-top:8px;font-size:13px}
.hidden{display:none!important}
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:var(--border2);border-radius:99px}
</style>
</head>
<body>

<!-- HEADER -->
<div class="header">
  <div class="header-inner">
    <div class="hdr-logo">🗺️</div>
    <div>
      <div class="hdr-title">SIS Application — Submission Channel Report</div>
      <div class="hdr-sub">Thoothukudi District · NISD / ISD · CSC · SRO · Citizen</div>
    </div>
    <div class="hdr-spacer"></div>
    <div class="nav-tabs">
      <a class="nav-tab active" href="#nisd-spotlight">NISD Spotlight</a>
      <a class="nav-tab" href="#channel-view">By Channel</a>
      <a class="nav-tab" href="#combo-section">Combinations</a>
    </div>
    <div class="hdr-pill" id="totalPill">— records</div>
  </div>
</div>

<div class="main">

<!-- HERO STATS -->
<div class="hero" id="heroStats"></div>

<!-- ═══════════════ NISD SPOTLIGHT ═══════════════ -->
<div id="nisd-spotlight" class="nisd-spotlight">
  <div class="nisd-spotlight-header">
    <span style="font-size:22px">📋</span>
    <div>
      <div class="nisd-spotlight-title">NISD Applications — Pending vs Completed</div>
      <div style="font-size:12px;color:var(--muted);margin-top:2px">Not Involving Sub-Division (Service Code 0153) · Document verification only · No field visit required</div>
    </div>
    <div class="nisd-spotlight-sub" id="nisdSubCount"></div>
  </div>

  <!-- pending vs completed split by channel -->
  <div class="nisd-status-grid" id="nisdStatusGrid"></div>

  <!-- NISD tables: pending then completed -->
  <div id="nisdTablesArea"></div>
</div>

<!-- ═══════════════ ISD SECTION ═══════════════ -->
<div id="isd-section" style="margin-top:28px">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
    <span style="font-size:20px">🏗️</span>
    <div style="font-size:15px;font-weight:800;color:var(--isd)">ISD Applications</div>
    <span style="font-size:11px;color:var(--muted)">Involving Sub-Division (0154) · Field visit + SD sketch required</span>
    <span id="isdTotalBadge" style="margin-left:auto;font-size:13px;font-weight:700;font-family:'JetBrains Mono',monospace;color:var(--isd)"></span>
  </div>
  <div id="isdTablesArea"></div>
</div>

<!-- ═══════════════ CHANNEL VIEW ═══════════════ -->
<div id="channel-view" style="margin-top:36px">
  <div style="font-size:15px;font-weight:800;margin-bottom:14px;display:flex;align-items:center;gap:8px">
    <span>📡</span> All Applications — By Channel
  </div>

  <!-- Channel cards -->
  <div class="channel-cards" id="channelCards"></div>

  <!-- Filter bar -->
  <div class="filter-bar">
    <span class="fl">Channel</span>
    <div class="fg" id="chF"></div>
    <div class="fsep"></div>
    <span class="fl">Type</span>
    <div class="fg" id="tpF"></div>
    <div class="fsep"></div>
    <span class="fl">Status</span>
    <div class="fg" id="stF"></div>
    <div class="fsep"></div>
    <div class="fsearch">
      <span class="fsearch-icon">🔍</span>
      <input type="text" id="searchBox" placeholder="Search app no., name, CAN, mobile…"/>
    </div>
    <span class="fcount" id="fcount"></span>
  </div>

  <!-- View tabs: by channel or by status-group -->
  <div class="type-tabs">
    <button class="tt tt-on all" id="ttAll"   onclick="setViewMode('all')">All</button>
    <button class="tt nisd"      id="ttNISD"  onclick="setViewMode('NISD')">NISD Only</button>
    <button class="tt isd"       id="ttISD"   onclick="setViewMode('ISD')">ISD Only</button>
  </div>

  <div id="tablesArea"></div>
</div>

<!-- ═══════════════ COMBINATION MATRIX ═══════════════ -->
<div class="combo-section" id="combo-section">
  <div class="combo-title">📊 All Channel × Type × Status Combinations</div>
  <div class="combo-tabs">
    <button class="ctab ctab-on" onclick="setComboView('all',this)">All</button>
    <button class="ctab" onclick="setComboView('NISD',this)">NISD</button>
    <button class="ctab" onclick="setComboView('ISD',this)">ISD</button>
    <button class="ctab" onclick="setComboView('multi',this)">Multi-Channel</button>
  </div>
  <div class="combo-grid" id="comboGrid"></div>
</div>
</div>

<!-- DETAIL DRAWER -->
<div class="overlay" id="overlay">
  <div class="drawer">
    <div class="drawer-handle"></div>
    <div class="drawer-hdr" id="drawerHdr"></div>
    <div class="drawer-grid" id="drawerGrid"></div>
    <button class="drawer-close" onclick="closeDrawer()">✕ Close</button>
  </div>
</div>

<script>
// ─── DATA ────────────────────────────────────────────────────────────────────
const RAW = """ + data_json + r""";

const CH_META = {
  CSC:           { label:'CSC / e-Sevai',        cls:'csc',     icon:'🏪', short:'CSC'     },
  sub_registrar: { label:'Sub-Registrar (SRO)',   cls:'sro',     icon:'🏛️', short:'SRO'     },
  citizen:       { label:'Citizen (Camp)',         cls:'citizen', icon:'👤', short:'Citizen' },
};
const ST_ICON = { approved:'✅', pending:'⏳', in_progress:'🔄', rejected:'❌' };
const ST_LABEL = { approved:'Approved', pending:'Pending', in_progress:'In Progress', rejected:'Rejected' };
const ALL_CH = ['CSC','sub_registrar','citizen'];
const ALL_TP = ['ISD','NISD'];
const ALL_ST = ['pending','in_progress','approved','rejected'];
const COMPLETED_ST = ['approved','rejected'];
const OPEN_ST = ['pending','in_progress'];

// ─── STATE ────────────────────────────────────────────────────────────────────
let activeCh  = new Set(ALL_CH);
let activeTp  = new Set(ALL_TP);
let activeSt  = new Set(ALL_ST);
let searchVal = '';
let viewMode  = 'all';   // 'all' | 'NISD' | 'ISD'
let comboView = 'all';

// ─── HELPERS ─────────────────────────────────────────────────────────────────
function fmt_date(d){ return d ? new Date(d).toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'}) : '—' }
function fmt_fee(f) { return f ? '₹'+parseFloat(f).toLocaleString('en-IN',{minimumFractionDigits:2}) : '—' }
function count(data, pred){ return data.filter(pred).length; }
function by(data, key, val){ return data.filter(r=>r[key]===val); }

// ─── HERO STATS ───────────────────────────────────────────────────────────────
function renderHero(){
  const nisd = by(RAW,'application_type','NISD');
  const isd  = by(RAW,'application_type','ISD');
  const all  = RAW;

  const items = [
    {
      cls:'nisd-hero', icon:'📋', label:'NISD Applications', num: nisd.length, numCls:'nisd-color',
      chips:[
        ...OPEN_ST.map(s=>({ st:s, n:by(nisd,'current_status',s).length })).filter(x=>x.n),
        ...COMPLETED_ST.map(s=>({ st:s, n:by(nisd,'current_status',s).length })).filter(x=>x.n),
      ],
      chChips: ALL_CH.map(ch=>({ch, n:by(nisd,'submission_channel',ch).length})).filter(x=>x.n)
    },
    {
      cls:'isd-hero', icon:'🏗️', label:'ISD Applications', num: isd.length, numCls:'isd-color',
      chips:[
        ...ALL_ST.map(s=>({ st:s, n:by(isd,'current_status',s).length })).filter(x=>x.n),
      ],
      chChips: ALL_CH.map(ch=>({ch, n:by(isd,'submission_channel',ch).length})).filter(x=>x.n)
    },
    {
      cls:'all-hero', icon:'🌐', label:'All Applications', num: all.length, numCls:'all-color',
      chips: ALL_ST.map(s=>({st:s, n:by(all,'current_status',s).length})).filter(x=>x.n),
      chChips: ALL_CH.map(ch=>({ch, n:by(all,'submission_channel',ch).length})).filter(x=>x.n)
    }
  ];

  document.getElementById('heroStats').innerHTML = items.map(item=>`
    <div class="hero-card ${item.cls}">
      <div class="hero-top">
        <span class="hero-icon">${item.icon}</span>
        <div>
          <div class="hero-label">${item.label}</div>
          <div class="hero-num ${item.numCls}">${item.num}</div>
        </div>
      </div>
      <div class="hero-chips">
        ${item.chips.map(x=>`<span class="hchip hchip-${x.st}">${ST_ICON[x.st]} ${x.n} ${ST_LABEL[x.st]}</span>`).join('')}
      </div>
      <div class="hero-chips" style="margin-top:6px">
        ${item.chChips.map(x=>`<span class="hchip hchip-${x.ch==='sub_registrar'?'sro':x.ch}">${CH_META[x.ch].icon} ${x.n} ${CH_META[x.ch].short}</span>`).join('')}
      </div>
    </div>
  `).join('');
  document.getElementById('totalPill').textContent = `${RAW.length} applications`;
}

// ─── NISD SPOTLIGHT ──────────────────────────────────────────────────────────
function renderNISDSpotlight(){
  const nisd = by(RAW,'application_type','NISD');
  document.getElementById('nisdSubCount').textContent = `${nisd.length} total NISD`;

  // Left: pending+in_progress | Right: approved+rejected
  const gridEl = document.getElementById('nisdStatusGrid');
  const makeHalf = (title, cls, statuses) => {
    const rows = ALL_CH.map(ch=>{
      const chData = by(nisd,'submission_channel',ch);
      const items  = statuses.map(st=>({st, n:by(chData,'current_status',st).length})).filter(x=>x.n);
      const total  = items.reduce((a,b)=>a+b.n,0);
      return {ch, items, total};
    }).filter(r=>r.total>0);
    const maxN = Math.max(...rows.map(r=>r.total),1);
    const barColors = {pending:'var(--pending)',in_progress:'var(--inprog)',approved:'var(--approved)',rejected:'var(--rejected)'};
    return `
      <div class="nisd-half">
        <div class="nisd-half-title ${cls}">${title === 'Pending / In-Progress' ? '⏳' : '✅'} ${title}</div>
        ${rows.map(r=>`
          <div class="nisd-channel-row">
            <span class="nisd-ch-icon">${CH_META[r.ch].icon}</span>
            <div style="flex:1">
              <div style="font-size:12px;font-weight:700;margin-bottom:4px">${CH_META[r.ch].short}</div>
              <div class="nisd-ch-bars">
                ${r.items.map(it=>`
                  <div class="nisd-bar-row">
                    <span style="width:70px;color:var(--muted)">${ST_LABEL[it.st]}</span>
                    <div class="nisd-bar-track"><div class="nisd-bar-fill" style="width:${Math.round(it.n/maxN*100)}%;background:${barColors[it.st]}"></div></div>
                    <span class="nisd-bar-val" style="color:${barColors[it.st]}">${it.n}</span>
                  </div>
                `).join('')}
              </div>
            </div>
            <div class="nisd-ch-total">${r.total}</div>
          </div>
        `).join('')}
        ${rows.length===0?`<div style="color:var(--muted);font-size:12px;padding:12px 0">None in this state</div>`:''}
      </div>
    `;
  };

  gridEl.innerHTML =
    makeHalf('Pending / In-Progress','pending-title', OPEN_ST) +
    makeHalf('Completed (Approved + Rejected)','completed-title', COMPLETED_ST);

  // NISD tables by channel → pending first, then completed
  const nisdArea = document.getElementById('nisdTablesArea');
  nisdArea.innerHTML = '';

  // Group: show pending first, then approved, then rejected — per channel
  const STATUS_ORDER = ['pending','in_progress','approved','rejected'];

  ALL_CH.forEach(ch=>{
    const chData = nisd.filter(r=>r.submission_channel===ch);
    if(!chData.length) return;
    const meta = CH_META[ch];

    // Section header
    const hdr = document.createElement('div');
    hdr.innerHTML = `
      <div class="section-hdr ${meta.cls}" style="margin-top:0;border-top:1px solid var(--border)">
        <span class="sh-icon">${meta.icon}</span>
        <span class="sh-label">${meta.label} — NISD</span>
        <span class="sh-badge">${chData.length} applications</span>
      </div>
    `;
    nisdArea.appendChild(hdr);

    const wrap = document.createElement('div');
    wrap.className = 'tbl-wrap';
    wrap.style.border = '1px solid var(--border)';
    wrap.style.borderTop = 'none';
    wrap.style.borderRadius = '0 0 12px 12px';
    wrap.style.marginBottom = '16px';

    STATUS_ORDER.forEach(st=>{
      const stData = chData.filter(r=>r.current_status===st);
      if(!stData.length) return;
      const dotCls = st==='in_progress'?'in_progress':st;

      const subHdr = document.createElement('div');
      subHdr.className='status-sub-hdr';
      subHdr.innerHTML=`
        <span class="ssh-dot dot-${dotCls}"></span>
        <span class="ssh-label ${st==='in_progress'?'in_progress':st}">${st==='in_progress'?'In Progress':ST_LABEL[st]} NISD</span>
        <span class="ssh-count">${stData.length} records</span>
      `;
      wrap.appendChild(subHdr);

      const tbl = buildTable(stData, true);
      wrap.appendChild(tbl);
    });

    nisdArea.appendChild(wrap);
  });
}

// ─── ISD SECTION ─────────────────────────────────────────────────────────────
function renderISDSection(){
  const isd = by(RAW,'application_type','ISD');
  document.getElementById('isdTotalBadge').textContent = `${isd.length} total`;
  const area = document.getElementById('isdTablesArea');
  area.innerHTML = '';

  ALL_CH.forEach(ch=>{
    const chData = isd.filter(r=>r.submission_channel===ch);
    if(!chData.length) return;
    const meta = CH_META[ch];

    const hdr = document.createElement('div');
    hdr.innerHTML = `
      <div class="section-hdr ${meta.cls}">
        <span class="sh-icon">${meta.icon}</span>
        <span class="sh-label">${meta.label} — ISD</span>
        <span class="sh-badge">${chData.length} applications</span>
      </div>
    `;
    area.appendChild(hdr);

    const wrap = document.createElement('div');
    wrap.className='tbl-wrap';
    wrap.style.border='1px solid var(--border)';
    wrap.style.borderTop='none';
    wrap.style.borderRadius='0 0 12px 12px';
    wrap.style.marginBottom='16px';

    ['pending','in_progress','approved','rejected'].forEach(st=>{
      const stData = chData.filter(r=>r.current_status===st);
      if(!stData.length) return;
      const hdr2 = document.createElement('div');
      hdr2.className='status-sub-hdr';
      hdr2.innerHTML=`
        <span class="ssh-dot dot-${st==='in_progress'?'in_progress':st}"></span>
        <span class="ssh-label ${st==='in_progress'?'in_progress':st}">${ST_LABEL[st]} ISD</span>
        <span class="ssh-count">${stData.length} records</span>
      `;
      wrap.appendChild(hdr2);
      wrap.appendChild(buildTable(stData, false));
    });
    area.appendChild(wrap);
  });
}

// ─── TABLE BUILDER ────────────────────────────────────────────────────────────
function buildTable(data, isNISD){
  const tbl = document.createElement('table');
  tbl.innerHTML=`
    <thead><tr>
      <th>#</th>
      <th>Application No.</th>
      <th>Type</th>
      <th>Status</th>
      <th>Channel</th>
      <th>Applicant</th>
      <th>Mobile</th>
      <th>Ward</th>
      <th>Survey No.</th>
      <th>CAN</th>
      <th>Date</th>
      <th>Fee</th>
      <th>${isNISD?'IGRS Form6':'Current Stage'}</th>
      <th>Source</th>
    </tr></thead>
    <tbody></tbody>
  `;
  const tbody = tbl.querySelector('tbody');
  if(!data.length){
    const tr=document.createElement('tr');
    tr.innerHTML=`<td colspan="14" class="empty-state" style="border:none;background:none">No records</td>`;
    tbody.appendChild(tr);
    return tbl;
  }
  data.forEach((r,i)=>{
    const chMeta = CH_META[r.submission_channel]||{icon:'📄',cls:'csc',short:r.submission_channel};
    const tr=document.createElement('tr');
    tr.innerHTML=`
      <td class="muted" style="font-size:11px">${i+1}</td>
      <td><span class="app-link" onclick='showDetail(${JSON.stringify(JSON.stringify(r))})'>${r.application_number}</span></td>
      <td><span class="badge b-${r.application_type}">${r.application_type}</span></td>
      <td><span class="badge b-${r.current_status}">${ST_ICON[r.current_status]||''} ${ST_LABEL[r.current_status]||r.current_status}</span></td>
      <td><span class="badge b-${r.submission_channel==='sub_registrar'?'sro':r.submission_channel}">${chMeta.icon} ${chMeta.short}</span></td>
      <td style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${r.applicant_name||''}">${r.applicant_name||'—'}</td>
      <td class="mono muted">${r.mobile_number||'—'}</td>
      <td><span class="ward-c">W-${r.ward_number||'?'}</span></td>
      <td><span class="sno-c">${r.survey_no||'—'}</span></td>
      <td class="mono muted" style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${r.can_number||'—'}</td>
      <td class="mono muted">${fmt_date(r.submission_date)}</td>
      <td class="fee">${fmt_fee(r.fee_amount)}</td>
      <td>${isNISD
            ? (r.igrs_form6_number?`<span class="igrs-c">${r.igrs_form6_number}</span>`:'<span class="muted">—</span>')
            : `<span class="stage-c">${r.current_stage||'—'}</span>`}</td>
      <td class="mono muted" style="max-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${r.submission_source_name||''}">${r.submission_source_name||'—'}</td>
    `;
    tbody.appendChild(tr);
  });
  return tbl;
}

// ─── CHANNEL CARDS ────────────────────────────────────────────────────────────
function renderChannelCards(){
  const el = document.getElementById('channelCards');
  el.innerHTML = '';
  ALL_CH.forEach(ch=>{
    const meta = CH_META[ch];
    const chData = by(RAW,'submission_channel',ch);
    const nisdN = by(chData,'application_type','NISD').length;
    const isdN  = by(chData,'application_type','ISD').length;
    const maxTP = Math.max(nisdN,isdN,1);
    const stChips = ALL_ST.map(s=>({s, n:by(chData,'current_status',s).length})).filter(x=>x.n);

    const card = document.createElement('div');
    card.className=`ch-card ${meta.cls} ${activeCh.has(ch)?'ch-active':''}`;
    card.id=`chCard_${ch}`;
    card.onclick=()=>toggleChannelCard(ch);
    card.innerHTML=`
      <div class="ch-card-bar"></div>
      <div class="ch-card-body">
        <div class="ch-card-top">
          <span class="ch-icon">${meta.icon}</span>
          <div>
            <div class="ch-name">${meta.label}</div>
            <div class="ch-desc">${ch==='sub_registrar'?'IGRS auto-mutation · No operator':ch==='CSC'?'Common Service Centre / e-Sevai':'Revenue camp filing'}</div>
          </div>
          <div class="ch-total">${chData.length}</div>
        </div>
        <div class="ch-type-grid">
          <div class="ch-type-row">
            <span class="ch-type-label" style="color:var(--nisd)">NISD</span>
            <div class="ch-track"><div class="ch-fill ch-fill-nisd" style="width:${Math.round(nisdN/maxTP*100)}%"></div></div>
            <span class="ch-type-count" style="color:var(--nisd)">${nisdN}</span>
          </div>
          <div class="ch-type-row">
            <span class="ch-type-label" style="color:var(--isd)">ISD</span>
            <div class="ch-track"><div class="ch-fill ch-fill-isd" style="width:${Math.round(isdN/maxTP*100)}%"></div></div>
            <span class="ch-type-count" style="color:var(--isd)">${isdN}</span>
          </div>
        </div>
        <div class="ch-status-pills">
          ${stChips.map(x=>`<span class="badge b-${x.s}">${ST_ICON[x.s]} ${x.n} ${ST_LABEL[x.s]}</span>`).join('')}
        </div>
      </div>
    `;
    el.appendChild(card);
  });
}
function toggleChannelCard(ch){
  if(activeCh.has(ch)){ if(activeCh.size>1){ activeCh.delete(ch); } }
  else { activeCh.add(ch); }
  renderChannelCards();
  renderFilters();
  renderMainTables();
}

// ─── FILTER BUTTONS ──────────────────────────────────────────────────────────
function renderFilters(){
  // Channel
  const chEl=document.getElementById('chF'); chEl.innerHTML='';
  [['CSC','CSC'],['sub_registrar','SRO'],['citizen','Citizen']].forEach(([val,lbl])=>{
    const b=document.createElement('button');
    b.className=`fb ch-${val} ${activeCh.has(val)?'on':''}`;
    b.textContent=lbl;
    b.onclick=()=>toggleFilter(activeCh,val,b,'ch');
    chEl.appendChild(b);
  });
  // Type
  const tpEl=document.getElementById('tpF'); tpEl.innerHTML='';
  ['NISD','ISD'].forEach(tp=>{
    const b=document.createElement('button');
    b.className=`fb tp-${tp} ${activeTp.has(tp)?'on':''}`;
    b.textContent=tp;
    b.onclick=()=>toggleFilter(activeTp,tp,b,'tp');
    tpEl.appendChild(b);
  });
  // Status
  const stEl=document.getElementById('stF'); stEl.innerHTML='';
  [['pending','Pending'],['in_progress','In Progress'],['approved','Approved'],['rejected','Rejected']].forEach(([val,lbl])=>{
    const b=document.createElement('button');
    b.className=`fb st-${val} ${activeSt.has(val)?'on':''}`;
    b.textContent=lbl;
    b.onclick=()=>toggleFilter(activeSt,val,b,'st');
    stEl.appendChild(b);
  });
}
function toggleFilter(set,val,btn,kind){
  if(set.has(val)){ if(set.size>1){ set.delete(val); btn.classList.remove('on'); } }
  else { set.add(val); btn.classList.add('on'); }
  if(kind==='ch') renderChannelCards();
  renderMainTables();
}

// ─── VIEW MODE ───────────────────────────────────────────────────────────────
function setViewMode(mode){
  viewMode=mode;
  ['all','NISD','ISD'].forEach(m=>{
    const el=document.getElementById(`tt${m==='all'?'All':m}`);
    if(el) el.classList.toggle('tt-on',m===mode);
  });
  renderMainTables();
}

// ─── MAIN TABLES ─────────────────────────────────────────────────────────────
function renderMainTables(){
  let data = RAW.filter(r=>
    activeCh.has(r.submission_channel) &&
    activeTp.has(r.application_type) &&
    activeSt.has(r.current_status) &&
    (!searchVal ||
      (r.application_number||'').toLowerCase().includes(searchVal)||
      (r.applicant_name||'').toLowerCase().includes(searchVal)||
      (r.can_number||'').toLowerCase().includes(searchVal)||
      (r.mobile_number||'').toLowerCase().includes(searchVal)
    )
  );
  if(viewMode!=='all') data=data.filter(r=>r.application_type===viewMode);

  document.getElementById('fcount').textContent = `${data.length} record${data.length!==1?'s':''}`;
  const area=document.getElementById('tablesArea');
  area.innerHTML='';

  if(!data.length){
    area.innerHTML='<div class="empty-state">⚠️ No applications match the current filters.</div>';
    return;
  }

  const channels=[...activeCh].sort();
  channels.forEach(ch=>{
    const chData = data.filter(r=>r.submission_channel===ch);
    if(!chData.length) return;
    const meta=CH_META[ch];

    const hdr=document.createElement('div');
    hdr.innerHTML=`<div class="section-hdr ${meta.cls}">
      <span class="sh-icon">${meta.icon}</span>
      <span class="sh-label">${meta.label}</span>
      <span class="sh-badge">${chData.length} records</span>
    </div>`;
    area.appendChild(hdr);

    const wrap=document.createElement('div');
    wrap.className='tbl-wrap';
    wrap.style.border='1px solid var(--border)';
    wrap.style.borderTop='none';
    wrap.style.borderRadius='0 0 12px 12px';
    wrap.style.marginBottom='16px';

    // Group by status in a sensible order
    ['pending','in_progress','approved','rejected'].forEach(st=>{
      if(!activeSt.has(st)) return;
      const stData=chData.filter(r=>r.current_status===st);
      if(!stData.length) return;
      const subHdr=document.createElement('div');
      subHdr.className='status-sub-hdr';
      subHdr.innerHTML=`
        <span class="ssh-dot dot-${st==='in_progress'?'in_progress':st}"></span>
        <span class="ssh-label ${st==='in_progress'?'in_progress':st}">${ST_LABEL[st]}</span>
        <span class="ssh-count">${stData.length} records</span>
      `;
      wrap.appendChild(subHdr);
      wrap.appendChild(buildTable(stData, false));
    });
    area.appendChild(wrap);
  });
}

// ─── COMBINATION MATRIX ───────────────────────────────────────────────────────
function buildCombos(){
  const all=[];
  const nisd=by(RAW,'application_type','NISD');
  const isd =by(RAW,'application_type','ISD');

  // Per channel per type with status breakdown
  ALL_CH.forEach(ch=>{
    ['NISD','ISD'].forEach(tp=>{
      const d=RAW.filter(r=>r.submission_channel===ch&&r.application_type===tp);
      if(!d.length) return;
      const pending=OPEN_ST.reduce((a,s)=>a+by(d,'current_status',s).length,0);
      const completed=COMPLETED_ST.reduce((a,s)=>a+by(d,'current_status',s).length,0);
      const chips=[
        ...OPEN_ST.map(s=>({cl:s,label:`${ST_LABEL[s]}: ${by(d,'current_status',s).length}`})).filter(x=>x.label.slice(-1)!=='0'),
        ...COMPLETED_ST.map(s=>({cl:s,label:`${ST_LABEL[s]}: ${by(d,'current_status',s).length}`})).filter(x=>x.label.slice(-1)!=='0'),
      ];
      all.push({
        view:tp, multiView:false,
        label:`${CH_META[ch].short} × ${tp}`,
        icon:`${CH_META[ch].icon}`,
        count:d.length, chips,
        sub:[`Open: ${pending}`,`Completed: ${completed}`],
        comboClass:tp==='NISD'?'nisd-combo':'isd-combo',
        filter:()=>{ activeCh=new Set([ch]); activeTp=new Set([tp]); activeSt=new Set(ALL_ST); renderChannelCards();renderFilters();renderMainTables(); setViewMode(tp); document.getElementById('channel-view').scrollIntoView({behavior:'smooth'}); }
      });
    });
  });

  // Per channel all types
  ALL_CH.forEach(ch=>{
    const d=by(RAW,'submission_channel',ch);
    const chips=ALL_TP.map(tp=>({cl:'',label:`${tp}: ${by(d,'application_type',tp).length}`}));
    all.push({
      view:'ch', multiView:false,
      label:`${CH_META[ch].short} — All`,
      icon:CH_META[ch].icon, count:d.length, chips,
      sub:ALL_ST.map(s=>`${ST_LABEL[s]}: ${by(d,'current_status',s).length}`),
      comboClass:'multi-combo',
      filter:()=>{ activeCh=new Set([ch]); activeTp=new Set(ALL_TP); activeSt=new Set(ALL_ST); renderChannelCards();renderFilters();renderMainTables(); setViewMode('all'); document.getElementById('channel-view').scrollIntoView({behavior:'smooth'}); }
    });
  });

  // Multi-channel NISD: CSC+SRO, CSC+Citizen, SRO+Citizen, All
  [
    {key:'csc_sro',     chs:['CSC','sub_registrar'], lbl:'CSC + SRO',      icon:'🏪🏛️'},
    {key:'csc_cit',     chs:['CSC','citizen'],        lbl:'CSC + Citizen',  icon:'🏪👤'},
    {key:'sro_cit',     chs:['sub_registrar','citizen'],lbl:'SRO + Citizen',icon:'🏛️👤'},
    {key:'all3',        chs:ALL_CH,                   lbl:'All Channels',   icon:'🌐'},
  ].forEach(g=>{
    ALL_TP.forEach(tp=>{
      const d=RAW.filter(r=>g.chs.includes(r.submission_channel)&&r.application_type===tp);
      if(!d.length) return;
      const pending=OPEN_ST.reduce((a,s)=>a+by(d,'current_status',s).length,0);
      const completed=COMPLETED_ST.reduce((a,s)=>a+by(d,'current_status',s).length,0);
      all.push({
        view:'multi', multiView:true,
        label:`${g.lbl} × ${tp}`,
        icon:g.icon, count:d.length,
        chips:[{cl:'pending',label:`Open: ${pending}`},{cl:'completed',label:`Completed: ${completed}`}],
        sub:ALL_ST.map(s=>({cl:s,label:`${ST_LABEL[s]}: ${by(d,'current_status',s).length}`})).filter(x=>x.label.slice(-1)!=='0').map(x=>x.label),
        comboClass:tp==='NISD'?'nisd-combo':'isd-combo',
        filter:()=>{ activeCh=new Set(g.chs); activeTp=new Set([tp]); activeSt=new Set(ALL_ST); renderChannelCards();renderFilters();renderMainTables(); setViewMode(tp); document.getElementById('channel-view').scrollIntoView({behavior:'smooth'}); }
      });
    });
    // all types
    const d=RAW.filter(r=>g.chs.includes(r.submission_channel));
    if(d.length){
      all.push({
        view:'multi', multiView:true,
        label:`${g.lbl} — All Types`,
        icon:g.icon, count:d.length,
        chips:ALL_TP.map(tp=>({cl:'',label:`${tp}: ${by(d,'application_type',tp).length}`})),
        sub:ALL_ST.map(s=>`${ST_LABEL[s]}: ${by(d,'current_status',s).length}`),
        comboClass:'multi-combo',
        filter:()=>{ activeCh=new Set(g.chs); activeTp=new Set(ALL_TP); activeSt=new Set(ALL_ST); renderChannelCards();renderFilters();renderMainTables(); setViewMode('all'); document.getElementById('channel-view').scrollIntoView({behavior:'smooth'}); }
      });
    }
  });

  return all;
}

let allCombos = [];
function setComboView(v, btn){
  comboView=v;
  document.querySelectorAll('.ctab').forEach(el=>el.classList.remove('ctab-on'));
  btn.classList.add('ctab-on');
  renderComboGrid();
}
function renderComboGrid(){
  const grid=document.getElementById('comboGrid');
  grid.innerHTML='';
  let items=allCombos;
  if(comboView==='NISD') items=items.filter(c=>c.label.includes('NISD'));
  else if(comboView==='ISD') items=items.filter(c=>c.label.includes('ISD')||c.label.includes('All'));
  else if(comboView==='multi') items=items.filter(c=>c.multiView);

  items.forEach(c=>{
    const card=document.createElement('div');
    card.className=`combo-card ${c.comboClass}`;
    card.onclick=c.filter;
    card.innerHTML=`
      <div class="combo-card-label"><span>${c.icon}</span><span>${c.label}</span></div>
      <div class="combo-card-count">${c.count}</div>
      <div class="combo-chips">${c.chips.map(ch=>typeof ch==='string'?`<span class="cc">${ch}</span>`:`<span class="cc ${ch.cl||''}">${ch.label}</span>`).join('')}</div>
      <div class="combo-chips" style="margin-top:4px">${(c.sub||[]).map(s=>typeof s==='string'?`<span class="cc">${s}</span>`:`<span class="cc ${s.cl||''}">${s.label}</span>`).join('')}</div>
    `;
    grid.appendChild(card);
  });
}

// ─── DETAIL DRAWER ────────────────────────────────────────────────────────────
function showDetail(jsonStr){
  const r=JSON.parse(jsonStr);
  const chMeta=CH_META[r.submission_channel]||{icon:'📄',label:r.submission_channel};
  document.getElementById('drawerHdr').innerHTML=`
    <div class="drawer-hdr-title">${r.application_number}</div>
    <span class="badge b-${r.application_type}">${r.application_type}</span>
    <span class="badge b-${r.current_status}">${ST_ICON[r.current_status]||''} ${ST_LABEL[r.current_status]||r.current_status}</span>
    <span class="badge b-${r.submission_channel==='sub_registrar'?'sro':r.submission_channel}">${chMeta.icon} ${chMeta.label}</span>
  `;
  const fields=[
    ['Application No.', r.application_number],
    ['Type', r.application_type],
    ['Status', ST_LABEL[r.current_status]||r.current_status],
    ['Channel', chMeta.label],
    ['Submission Date', fmt_date(r.submission_date)],
    ['Applicant Name', r.applicant_name||'—'],
    ['Mobile', r.mobile_number||'—'],
    ['Gender', r.gender||'—'],
    ['Occupation', r.occupation||'—'],
    ['CAN Number', r.can_number||'—'],
    ['Ward', r.ward_number?`Ward ${r.ward_number}`:'—'],
    ['Survey No.', r.survey_no||'—'],
    ['Patta No.', r.patta_number||'—'],
    ['Area (sqm)', r.total_area_sqm||'—'],
    ['Fee Amount', fmt_fee(r.fee_amount)],
    ['Payment Mode', r.payment_mode||'—'],
    ['Challan No.', r.challan_number||'—'],
    ['IGRS Form6', r.igrs_form6_number||'—'],
    ['Current Stage', r.current_stage||'—'],
    ['Declared Reason', r.declared_reason||'—'],
    ['Source Name', r.submission_source_name||'—'],
    ['Camp Flag', r.submission_camp_flag||'—'],
  ];
  document.getElementById('drawerGrid').innerHTML=fields.map(([l,v])=>`
    <div class="df"><div class="df-label">${l}</div><div class="df-val">${v}</div></div>
  `).join('');
  document.getElementById('overlay').classList.add('open');
}
function closeDrawer(){document.getElementById('overlay').classList.remove('open')}
document.getElementById('overlay').addEventListener('click',function(e){if(e.target===this)closeDrawer()});

// ─── SEARCH ───────────────────────────────────────────────────────────────────
document.getElementById('searchBox').addEventListener('input',function(){
  searchVal=this.value.toLowerCase().trim();
  renderMainTables();
});

// ─── INIT ─────────────────────────────────────────────────────────────────────
renderHero();
renderNISDSpotlight();
renderISDSection();
renderChannelCards();
renderFilters();
renderMainTables();
allCombos=buildCombos();
renderComboGrid();
</script>
</body>
</html>
"""

out = "frontend/channel_report.html"
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print(f"Written: {out}")
