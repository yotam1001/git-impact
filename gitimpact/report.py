"""Portable terminal, JSON and self-contained HTML output."""

from html import escape


def terminal(report, show_diff=False):
    lines = ["GIT IMPACT", report["command"], "",
             f"{report['branch']}: {report['head_before'][:8]} -> {report['head_after'][:8]}",
             f"{report['commits_removed_from_current_history']} commit(s) leave the current history.", ""]
    for label, key in (("STAGING AREA", "index_changes"), ("WORKING TREE", "working_tree_changes")):
        changes = report[key]
        lines.append(f"{label} / {len(changes)} file(s)")
        if not changes:
            lines.append("  Unchanged")
        for change in changes:
            tag = " [untracked obstruction]" if key == "working_tree_changes" and not change["was_tracked"] and change["before"] else ""
            lines.append(f"  {change['kind']:8} {change['path']!r}{tag}")
            if show_diff:
                lines.extend("    " + line for line in change["diff"].splitlines())
        lines.append("")
    lines.append("Source repository unchanged. This command was run only in a temporary sandbox.")
    lines.append("Re-run the preview if repository state changes. This tool does not execute the command in your repository.")
    text = "\n".join(lines) + "\n"
    return "".join(c if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127)
                   else "\\x" + format(ord(c), "02x") for c in text)


CSS = """
:root{color-scheme:light;--ink:#20221d;--muted:#6a6f63;--line:#dfe2d7;--green:#386944;--paper:#f8f9f3}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}
.wrap{max-width:1080px;margin:auto;padding:38px 30px 65px}.top{display:flex;align-items:center;justify-content:space-between;gap:16px;border-bottom:1px solid var(--line);padding-bottom:24px}
.brand{font-size:19px;font-weight:750;letter-spacing:-.5px}.brand b{display:inline-grid;place-items:center;width:30px;height:30px;margin-right:10px;background:var(--ink);color:#eff6d9;border-radius:8px;font-size:18px}
.badge{font-size:11px;letter-spacing:1.2px;font-weight:700;background:#e8efdf;color:var(--green);border:1px solid #d3dfc9;padding:5px 10px;border-radius:20px}
.eyebrow{color:var(--muted);font-size:11px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;margin-top:40px}h1{font-size:clamp(30px,5vw,46px);line-height:1.12;letter-spacing:-1.9px;margin:13px 0 16px;max-width:800px}
.intro{color:var(--muted);max-width:730px;margin:0 0 24px}.command{background:var(--ink);color:#f5f6e9;border-radius:12px;padding:20px 24px;overflow:auto;font:16px/1.5 ui-monospace,Consolas,monospace}.command span{color:#b4c590;margin-right:15px}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:23px 0 30px}.stat{background:#fff;border:1px solid var(--line);border-radius:12px;padding:19px 22px}.number{display:block;font-size:31px;line-height:1.2;font-weight:650;letter-spacing:-1px}.label{font-size:12px;color:var(--muted)}
.meta{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin:16px 0 26px}code{font-family:ui-monospace,Consolas,monospace}.meta code{color:var(--ink)}
.tabs{display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap}button{font:inherit;font-size:12px;border:1px solid var(--line);background:transparent;color:var(--muted);padding:8px 13px;border-radius:7px;cursor:pointer}button[aria-pressed=true]{background:var(--ink);border-color:var(--ink);color:white}button:focus-visible,summary:focus-visible{outline:3px solid #95ab65;outline-offset:3px}
.section{margin:25px 0}h2{font-size:18px;letter-spacing:-.3px;margin:0 0 3px}.description{color:var(--muted);font-size:13px;margin:0 0 14px}.change{background:#fff;border:1px solid var(--line);border-radius:10px;margin:9px 0;overflow:hidden}summary{display:flex;align-items:center;gap:13px;padding:15px 18px;cursor:pointer;list-style:none}summary::-webkit-details-marker{display:none}summary:before{content:'+';font:18px ui-monospace,monospace;color:var(--muted)}details[open] summary:before{content:'−'}.path{flex:1;overflow-wrap:anywhere;font:13px/1.5 ui-monospace,Consolas,monospace}.kind{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;padding:3px 7px;border-radius:4px;background:#edf0e5;white-space:nowrap}.kind.deleted{background:#f9e7df;color:#a34b30}.kind.modified{background:#f7eed4;color:#8b6c20}.obstruction{font-size:10px;color:#a34b30;font-weight:700}
pre{margin:0;padding:18px 22px;border-top:1px solid var(--line);background:#f2f4ee;overflow:auto;font:12px/1.7 ui-monospace,Consolas,monospace;tab-size:4;max-height:480px}.empty{border:1px dashed var(--line);padding:16px 20px;border-radius:8px;color:var(--muted);font-size:13px}.commits{padding:15px 20px;background:#fff;border:1px solid var(--line);border-radius:10px;font-size:13px}.commit{margin:7px 0;overflow-wrap:anywhere}.commit code{color:var(--green);margin-right:12px}.notes{border-top:1px solid var(--line);margin-top:34px;padding-top:22px;font-size:12px;color:var(--muted)}.notes p{margin:6px 0}.footer{margin-top:20px;font-size:11px;color:var(--muted)}[hidden]{display:none!important}
@media(max-width:600px){.wrap{padding:22px 18px}.stats{gap:8px}.stat{padding:13px}.number{font-size:26px}summary{gap:8px;padding:13px}.badge{font-size:9px}.obstruction{display:none}.command{font-size:13px;padding:16px}.top{align-items:flex-start}}
@media(prefers-reduced-motion:no-preference){button{transition:background .12s}}
"""


def html_report(report):
    e = lambda value: escape(str(value), quote=True)
    sections = []
    specs = [("staging", "Staging area", "The versions staged for your next commit.", "index_changes"),
             ("working", "Working tree", "The files on disk, including untracked obstructions.", "working_tree_changes")]
    for category, title, description, key in specs:
        cards = []
        for change in report[key]:
            obstruction = key == "working_tree_changes" and not change["was_tracked"] and change["before"]
            tag = '<span class="obstruction">UNTRACKED OBSTRUCTION</span>' if obstruction else ""
            cards.append(f'<details class="change"><summary><span class="path">{e(change["path"])}</span>{tag}'
                         f'<span class="kind {e(change["kind"])}">{e(change["kind"])}</span></summary>'
                         f'<pre>{e(change["diff"])}</pre></details>')
        content = "".join(cards) or '<div class="empty">Unchanged by this command.</div>'
        sections.append(f'<section class="section" data-category="{category}"><h2>{title}</h2>'
                        f'<p class="description">{description}</p>{content}</section>')
    commits = "".join(f'<div class="commit"><code>{e(c["oid"][:8])}</code>{e(c["subject"])}</div>' for c in report["commits"])
    count = report["commits_removed_from_current_history"]
    if count > len(report["commits"]):
        commits += f'<p>Showing {len(report["commits"])} of {count} commits.</p>'
    if not commits:
        commits = '<div class="empty">No commits leave the current history.</div>'
    else:
        commits = '<div class="commits">' + commits + '</div>'
    sections.append('<section class="section" data-category="history"><h2>Current history</h2>'
                    '<p class="description">These commits leave this branch’s history. Other refs or the reflog may retain them.</p>' + commits + '</section>')
    notes = "".join('<p>' + e(note) + '</p>' for note in report["notes"])
    stats = [(len(report["index_changes"]), "staged files change"),
             (len(report["working_tree_changes"]), "files on disk change"), (count, "commits leave history")]
    stats_html = "".join(f'<div class="stat"><span class="number">{number}</span><span class="label">{label}</span></div>' for number, label in stats)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Git Impact · {e(report["command"])}</title><style>{CSS}</style></head>
<body><main class="wrap"><header class="top"><div class="brand"><b aria-hidden="true">↗</b>Git Impact</div><span class="badge">READ-ONLY PREVIEW</span></header>
<div class="eyebrow">{e(report["repository"])} / command preview</div><h1>Know what changes.<br>Before you run it.</h1>
<p class="intro">Git ran this command in a temporary sandbox. Your source repository was left unchanged. Review the effects on each part of your repository below.</p>
<div class="command"><span aria-hidden="true">$</span>{e(report["command"])}</div>
<div class="stats">{stats_html}</div><div class="meta"><span>Branch <code>{e(report["branch"])}</code></span><span>HEAD <code>{e(report["head_before"][:8])} → {e(report["head_after"][:8])}</code></span><span>Reset mode <code>{e(report["mode"])}</code></span></div>
<nav class="tabs" aria-label="Report sections"><button type="button" data-view="all" aria-pressed="true">All effects</button><button type="button" data-view="staging" aria-pressed="false">Staging area</button><button type="button" data-view="working" aria-pressed="false">Working tree</button><button type="button" data-view="history" aria-pressed="false">History</button></nav>
{''.join(sections)}<aside class="notes"><p><strong>Read the preview against the current state.</strong></p>{notes}</aside>
<footer class="footer">Generated locally with Git Impact {e(report["version"])} · No account, API, or hosted service.</footer></main>
<script>document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>{{document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));document.querySelectorAll('[data-category]').forEach(s=>s.hidden=button.dataset.view!=='all'&&s.dataset.category!==button.dataset.view)}}));</script></body></html>'''
