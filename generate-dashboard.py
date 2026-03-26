#!/usr/bin/env python3
"""
Dashboard de QA — Claint
Gera HTML standalone com dados do Jira.

Uso:
  1. Buscar dados:  curl ... > bugs-raw.json  (ou rodar com --fetch)
  2. Gerar HTML:    python generate-dashboard.py
  3. Abrir:         start dashboard.html
"""

import subprocess
import json
import base64
import os
import sys
import urllib.parse
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

# --- Config ---
JIRA_URL = "https://claint.atlassian.net"
JIRA_USER = "marcelo@claint.ai"
MCP_JSON = os.path.expanduser("~/.claude/.mcp.json")
SCRIPT_DIR = Path(__file__).parent
RAW_JSON = SCRIPT_DIR / "bugs-raw.json"
OUTPUT = SCRIPT_DIR / "dashboard.html"

# --- Auth ---
def get_token():
    with open(MCP_JSON) as f:
        d = json.load(f)
    args = d["mcpServers"]["atlassian"]["args"]
    return args[args.index("--jira-token") + 1]

# --- Jira fetch via curl (more reliable on Windows) ---
def fetch_all_bugs():
    token = get_token()
    auth = base64.b64encode(f"{JIRA_USER}:{token}".encode()).decode()
    jql = "project=CLAINT AND issuetype=Bug ORDER BY created DESC"
    fields = "summary,status,priority,labels,created,updated,parent,customfield_10016,customfield_10020,assignee,resolution"
    all_issues = []
    next_token = None
    page = 0

    while True:
        page += 1
        url = f"{JIRA_URL}/rest/api/3/search/jql?jql={urllib.parse.quote(jql)}&maxResults=100&fields={fields}"
        if next_token:
            url += f"&nextPageToken={urllib.parse.quote(next_token)}"

        result = subprocess.run(
            ["curl", "-s", "-H", f"Authorization: Basic {auth}", "-H", "Content-Type: application/json", url],
            capture_output=True, timeout=30
        )
        data = json.loads(result.stdout.decode("utf-8"))
        issues = data.get("issues", [])
        all_issues.extend(issues)
        is_last = data.get("isLast", True)
        next_token = data.get("nextPageToken", "")
        print(f"  Pagina {page}: +{len(issues)} bugs (acumulado: {len(all_issues)}, isLast={is_last})")

        if is_last or not issues or not next_token:
            break

    # Dedup by key (safety)
    seen = set()
    unique = []
    for i in all_issues:
        if i["key"] not in seen:
            seen.add(i["key"])
            unique.append(i)

    # Save for offline use
    output = {"issues": unique, "isLast": True, "totalFetched": len(unique)}
    RAW_JSON.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
    return unique

def load_from_file():
    with open(RAW_JSON, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("issues", [])

# --- Process ---
def process_bugs(raw):
    bugs = []
    for i in raw:
        f = i["fields"]
        sprint_data = f.get("customfield_10020") or []
        sprint = "-"
        if sprint_data and isinstance(sprint_data, list) and len(sprint_data) > 0:
            last = sprint_data[-1]
            sprint = last.get("name", "-") if isinstance(last, dict) else str(last)

        parent_key = f.get("parent", {}).get("key", "") if f.get("parent") else ""
        parent_summary = ""
        if f.get("parent") and f["parent"].get("fields"):
            parent_summary = f["parent"]["fields"].get("summary", "")

        bugs.append({
            "key": i["key"],
            "summary": f.get("summary", ""),
            "status": f.get("status", {}).get("name", ""),
            "priority": f.get("priority", {}).get("name", ""),
            "labels": f.get("labels", []),
            "epic_key": parent_key,
            "epic_name": parent_summary,
            "sp": f.get("customfield_10016") or 0,
            "assignee": f.get("assignee", {}).get("displayName", "Sem atribuicao") if f.get("assignee") else "Sem atribuicao",
            "sprint": sprint,
            "resolution": f.get("resolution", {}).get("name", "Unresolved") if f.get("resolution") else "Unresolved",
            "created": f.get("created", "")[:10],
            "updated": f.get("updated", "")[:10],
        })
    return bugs

def compute_metrics(bugs):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    total = len(bugs)

    # Status mapping
    status_map = defaultdict(int)
    for b in bugs:
        status_map[b["status"]] += 1

    resolved_statuses = {"Concluido", "Concluído", "Done", "Itens concluídos"}
    resolved = sum(1 for b in bugs if b["resolution"] != "Unresolved")
    open_bugs = total - resolved

    # Priority (total + split open/resolved)
    prio_map = defaultdict(int)
    prio_open = defaultdict(int)
    prio_resolved = defaultdict(int)
    for b in bugs:
        prio_map[b["priority"]] += 1
        if b["resolution"] == "Unresolved":
            prio_open[b["priority"]] += 1
        else:
            prio_resolved[b["priority"]] += 1

    # By feature (epic)
    feature_map = defaultdict(lambda: {"total": 0, "resolved": 0, "open": 0, "sp_total": 0, "sp_resolved": 0, "highest_high_open": 0, "bugs": []})
    for b in bugs:
        fname = b["epic_name"] or "Sem Epic"
        fm = feature_map[fname]
        fm["total"] += 1
        fm["sp_total"] += b["sp"]
        fm["bugs"].append(b)
        if b["resolution"] != "Unresolved":
            fm["resolved"] += 1
            fm["sp_resolved"] += b["sp"]
        else:
            fm["open"] += 1
            if b["priority"] in ("Highest", "High"):
                fm["highest_high_open"] += 1

    # By assignee
    assignee_map = defaultdict(lambda: {"total": 0, "resolved": 0, "open": 0})
    for b in bugs:
        a = b["assignee"]
        assignee_map[a]["total"] += 1
        if b["resolution"] != "Unresolved":
            assignee_map[a]["resolved"] += 1
        else:
            assignee_map[a]["open"] += 1

    # By sprint
    sprint_map = defaultdict(lambda: {"total": 0, "resolved": 0, "open": 0})
    for b in bugs:
        s = b["sprint"]
        sprint_map[s]["total"] += 1
        if b["resolution"] != "Unresolved":
            sprint_map[s]["resolved"] += 1
        else:
            sprint_map[s]["open"] += 1

    # By creation date
    daily_created = defaultdict(int)
    for b in bugs:
        daily_created[b["created"]] += 1

    # Critical open bugs (High/Highest + unresolved)
    critical_open = [b for b in bugs if b["resolution"] == "Unresolved" and b["priority"] in ("Highest", "High")]

    # Aging (days open)
    today = datetime.now(timezone.utc).date()
    for b in bugs:
        if b["created"]:
            created_date = datetime.strptime(b["created"], "%Y-%m-%d").date()
            b["age_days"] = (today - created_date).days
        else:
            b["age_days"] = 0

    # Open bugs sorted by age
    open_bugs_list = sorted(
        [b for b in bugs if b["resolution"] == "Unresolved"],
        key=lambda x: (-x.get("sp", 0), -x["age_days"])
    )

    return {
        "generated_at": now,
        "total": total,
        "resolved": resolved,
        "open": open_bugs,
        "completion_pct": round(resolved / total * 100, 1) if total > 0 else 0,
        "status_map": dict(status_map),
        "prio_map": dict(prio_map),
        "prio_open": dict(prio_open),
        "prio_resolved": dict(prio_resolved),
        "feature_map": {k: {kk: vv for kk, vv in v.items() if kk != "bugs"} for k, v in feature_map.items()},
        "assignee_map": dict(assignee_map),
        "sprint_map": dict(sprint_map),
        "daily_created": dict(sorted(daily_created.items())),
        "critical_open": critical_open,
        "critical_open_count": len(critical_open),
        "open_bugs_list": open_bugs_list,
        "all_bugs": bugs,
    }

# --- HTML Generation ---
def generate_html(metrics):
    m = metrics
    feature_data = sorted(m["feature_map"].items(), key=lambda x: -x[1]["total"])

    # Feature chart data
    feature_names = [f[0] for f in feature_data]
    feature_resolved = [f[1]["resolved"] for f in feature_data]
    feature_open = [f[1]["open"] for f in feature_data]

    # Feature completion (count-based — SP coverage is inconsistent across older bugs)
    feature_completion = []
    for name, data in feature_data:
        pct = round(data["resolved"] / data["total"] * 100, 1) if data["total"] > 0 else 0
        feature_completion.append({"name": name, "pct": pct, "total": data["total"], "resolved": data["resolved"], "open": data["open"], "hh_open": data["highest_high_open"], "sp_open": data["sp_total"] - data["sp_resolved"]})

    # Priority chart (stacked: open + resolved)
    prio_order = ["Highest", "High", "Medium", "Low", "Lowest"]
    prio_labels = [p for p in prio_order if p in m["prio_map"]]
    prio_values = [m["prio_map"].get(p, 0) for p in prio_labels]
    prio_open_values = [m["prio_open"].get(p, 0) for p in prio_labels]
    prio_resolved_values = [m["prio_resolved"].get(p, 0) for p in prio_labels]
    prio_colors = {
        "Highest": "#dc2626", "High": "#f97316", "Medium": "#eab308",
        "Low": "#22c55e", "Lowest": "#64748b"
    }
    # Dimmed versions for resolved
    prio_colors_dim = {
        "Highest": "#7f1d1d", "High": "#7c2d12", "Medium": "#713f12",
        "Low": "#14532d", "Lowest": "#334155"
    }

    # Status chart
    status_labels = list(m["status_map"].keys())
    status_values = list(m["status_map"].values())
    status_colors_map = {
        "Concluído": "#22c55e", "Tarefas pendentes": "#f97316",
        "Em andamento": "#3b82f6", "Ready to deploy": "#8b5cf6",
        "Ready to test": "#06b6d4",
    }

    # Assignee data
    assignee_data = sorted(m["assignee_map"].items(), key=lambda x: -x[1]["total"])

    # Sprint data
    sprint_order = ["#41", "#42", "43", "Bugs Claint", "Melhorias Claint"]
    sprint_data = [(s, m["sprint_map"].get(s, {"total": 0, "resolved": 0, "open": 0})) for s in sprint_order if s in m["sprint_map"]]
    # Add any sprints not in order
    for s, d in m["sprint_map"].items():
        if s not in sprint_order and s != "-":
            sprint_data.append((s, d))

    # Open bugs table rows
    open_rows = ""
    for b in m["open_bugs_list"][:50]:
        prio_color = prio_colors.get(b["priority"], "#64748b")
        age_class = "text-red-400 font-bold" if b["age_days"] >= 5 and b["priority"] in ("Highest", "High") else "text-zinc-400"
        sp_display = f'{b["sp"]:.0f}' if b["sp"] else "-"
        open_rows += f"""
        <tr class="border-b border-zinc-800 hover:bg-zinc-800/50 transition-colors">
            <td class="py-3 px-4"><a href="{JIRA_URL}/browse/{b['key']}" target="_blank" class="text-blue-400 hover:text-blue-300 font-mono text-sm">{b['key']}</a></td>
            <td class="py-3 px-4 text-sm max-w-xs truncate">{b['summary']}</td>
            <td class="py-3 px-4 text-sm">{b['epic_name'] or '-'}</td>
            <td class="py-3 px-4"><span class="inline-block w-3 h-3 rounded-full mr-2" style="background:{prio_color}"></span><span class="text-sm">{b['priority']}</span></td>
            <td class="py-3 px-4 text-sm font-mono">{sp_display}</td>
            <td class="py-3 px-4 text-sm">{b['status']}</td>
            <td class="py-3 px-4 text-sm">{b['assignee']}</td>
            <td class="py-3 px-4 text-sm {age_class}">{b['age_days']}d</td>
        </tr>"""

    # Feature completion bars
    feature_bars = ""
    for fc in sorted(feature_completion, key=lambda x: -x["total"]):
        bar_color = "#22c55e" if fc["pct"] >= 80 else "#eab308" if fc["pct"] >= 50 else "#f97316"
        risk_badge = f'<span class="ml-2 inline-flex items-center gap-1 text-xs bg-red-500/20 text-red-400 px-2 py-0.5 rounded-full"><svg width="10" height="10" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2L1 18h18L10 2zm0 4l6.5 11h-13L10 6z"/><rect x="9" y="9" width="2" height="4"/><rect x="9" y="14" width="2" height="2"/></svg>{fc["hh_open"]} High+</span>' if fc["hh_open"] > 0 else ""
        open_indicator = f'<span class="text-xs text-zinc-500 ml-1">({fc["open"]} abertos)</span>' if fc["open"] > 0 else ""
        # Background bar with two segments: resolved (green) + open (dark)
        feature_bars += f"""
        <div class="mb-3 py-2 px-3 rounded-lg hover:bg-zinc-800/40 transition-colors">
            <div class="flex justify-between items-center mb-1.5">
                <div class="flex items-center gap-2">
                    <span class="text-sm font-medium">{fc['name']}</span>
                    {risk_badge}
                </div>
                <div class="flex items-center gap-2">
                    <span class="text-sm font-mono" style="color:{bar_color}">{fc['pct']}%</span>
                    <span class="text-xs text-zinc-500">{fc['resolved']}/{fc['total']}</span>
                </div>
            </div>
            <div class="w-full bg-zinc-800 rounded-full h-2.5 overflow-hidden">
                <div class="h-2.5 rounded-full transition-all" style="width: {max(fc['pct'], 1.5)}%; background: {bar_color}"></div>
            </div>
        </div>"""

    # Assignee cards
    assignee_cards = ""
    for name, data in assignee_data:
        if name == "Sem atribuicao" or name == "N/A":
            continue
        pct = round(data["resolved"] / data["total"] * 100) if data["total"] > 0 else 0
        assignee_cards += f"""
        <div class="bg-zinc-800/50 rounded-xl p-4 border border-zinc-700">
            <div class="text-sm font-medium mb-2">{name}</div>
            <div class="flex items-baseline gap-2">
                <span class="text-2xl font-bold">{data['total']}</span>
                <span class="text-zinc-400 text-sm">bugs</span>
            </div>
            <div class="flex gap-4 mt-2 text-xs">
                <span class="text-green-400">{data['resolved']} resolvidos</span>
                <span class="text-orange-400">{data['open']} abertos</span>
            </div>
            <div class="w-full bg-zinc-700 rounded-full h-1.5 mt-2">
                <div class="h-1.5 rounded-full bg-green-500" style="width: {pct}%"></div>
            </div>
        </div>"""

    # Unassigned count
    unassigned = m["assignee_map"].get("Sem atribuicao", {}).get("open", 0) + m["assignee_map"].get("N/A", {}).get("open", 0)

    # Alerts
    alerts = []
    # Highest open > 0
    highest_open = sum(1 for b in m["open_bugs_list"] if b["priority"] == "Highest")
    if highest_open > 0:
        alerts.append(("red", f"{highest_open} bug(s) Highest aberto(s) — resolver imediatamente"))
    # High open > 10
    high_open = sum(1 for b in m["open_bugs_list"] if b["priority"] == "High")
    if high_open > 5:
        alerts.append(("orange", f"{high_open} bugs High abertos — alocar capacidade de fix"))
    # Unassigned
    if unassigned > 5:
        alerts.append(("yellow", f"{unassigned} bugs abertos sem responsavel — triagem necessaria"))
    # Feature with >3 HH open
    for fc in feature_completion:
        if fc["hh_open"] >= 3:
            alerts.append(("orange", f"{fc['name']}: {fc['hh_open']} bugs High+ abertos — considerar sprint de estabilizacao"))
    # Aging
    old_bugs = sum(1 for b in m["open_bugs_list"] if b["age_days"] >= 7)
    if old_bugs > 0:
        alerts.append(("yellow", f"{old_bugs} bug(s) aberto(s) ha mais de 7 dias — revisar se ainda sao validos"))

    alerts_html = ""
    icons = {
        "red": '<svg class="w-5 h-5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
        "orange": '<svg class="w-5 h-5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/></svg>',
        "yellow": '<svg class="w-5 h-5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
    }
    styles = {
        "red": "bg-red-500/10 border-l-4 border-red-500 text-red-400",
        "orange": "bg-orange-500/8 border-l-4 border-orange-500 text-orange-400",
        "yellow": "bg-yellow-500/8 border-l-4 border-yellow-500 text-yellow-400",
    }
    for color, msg in alerts:
        alerts_html += f'<div class="flex items-center gap-3 px-4 py-3 rounded-r-lg {styles[color]} text-sm">{icons[color]} <span>{msg}</span></div>'

    # All bugs JSON for client-side filtering
    all_bugs_json = json.dumps(m["all_bugs"], ensure_ascii=False, default=str)

    html = f"""<!DOCTYPE html>
<html lang="pt-BR" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard QA — Claint</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
    <style>
        body {{ background: #09090b; color: #fafafa; font-family: 'Inter', system-ui, sans-serif; }}
        .card {{ background: #18181b; border: 1px solid #27272a; border-radius: 12px; }}
        ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
        ::-webkit-scrollbar-track {{ background: #18181b; }}
        ::-webkit-scrollbar-thumb {{ background: #3f3f46; border-radius: 3px; }}
        .glow-green {{ box-shadow: 0 0 20px rgba(34, 197, 94, 0.1); }}
        .glow-red {{ box-shadow: 0 0 20px rgba(239, 68, 68, 0.1); }}
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(8px); }} to {{ opacity: 1; transform: translateY(0); }} }}
        .fade-in {{ animation: fadeIn 0.4s ease-out; }}
    </style>
</head>
<body class="min-h-screen p-6">
    <div class="max-w-7xl mx-auto fade-in">
        <!-- Header -->
        <div class="flex items-center justify-between mb-8">
            <div>
                <h1 class="text-3xl font-bold tracking-tight">Dashboard QA</h1>
                <p class="text-zinc-400 mt-1">Claint — Panorama de Qualidade</p>
            </div>
            <div class="text-right text-sm text-zinc-500">
                <div>Gerado em {m['generated_at']}</div>
                <div class="mt-1"><code class="bg-zinc-800 px-2 py-0.5 rounded text-xs">python generate-dashboard.py</code> para atualizar</div>
            </div>
        </div>

        <!-- KPI Cards -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <div class="card p-5">
                <div class="text-zinc-400 text-sm mb-1">Total de Bugs</div>
                <div class="text-4xl font-bold">{m['total']}</div>
                <div class="text-zinc-500 text-xs mt-1">reportados no Jira</div>
            </div>
            <div class="card p-5 {'glow-green' if m['completion_pct'] >= 60 else 'glow-red'}">
                <div class="text-zinc-400 text-sm mb-1">Taxa de Resolucao</div>
                <div class="text-4xl font-bold {'text-green-400' if m['completion_pct'] >= 60 else 'text-orange-400'}">{m['completion_pct']}%</div>
                <div class="text-zinc-500 text-xs mt-1">{m['resolved']} resolvidos / {m['open']} abertos</div>
            </div>
            <div class="card p-5 {'glow-red' if m['critical_open_count'] > 5 else ''}">
                <div class="text-zinc-400 text-sm mb-1">Criticos Abertos</div>
                <div class="text-4xl font-bold text-red-400">{m['critical_open_count']}</div>
                <div class="text-zinc-500 text-xs mt-1">High + Highest sem resolucao</div>
            </div>
            <div class="card p-5">
                <div class="text-zinc-400 text-sm mb-1">Sem Responsavel</div>
                <div class="text-4xl font-bold {'text-orange-400' if unassigned > 5 else 'text-zinc-300'}">{unassigned}</div>
                <div class="text-zinc-500 text-xs mt-1">bugs abertos nao atribuidos</div>
            </div>
        </div>

        <!-- Charts Row 1 -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
            <!-- Status Donut -->
            <div class="card p-6">
                <h2 class="text-lg font-semibold mb-4">Por Status</h2>
                <div class="flex items-center justify-center" style="height:280px">
                    <canvas id="statusChart"></canvas>
                </div>
            </div>
            <!-- Priority Bar -->
            <div class="card p-6">
                <h2 class="text-lg font-semibold mb-4">Por Prioridade</h2>
                <div style="height:280px">
                    <canvas id="priorityChart"></canvas>
                </div>
            </div>
        </div>

        <!-- Feature Completion -->
        <div class="card p-6 mb-8">
            <h2 class="text-lg font-semibold mb-2">Completude por Feature</h2>
            <p class="text-zinc-500 text-sm mb-4">Percentual de bugs resolvidos por feature (resolvidos/total). Badges vermelhos indicam quantidade de bugs High ou Highest ainda abertos.</p>
            {feature_bars}
        </div>

        <!-- Charts Row 2 -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
            <!-- Feature Stacked Bar -->
            <div class="card p-6">
                <h2 class="text-lg font-semibold mb-4">Bugs por Feature</h2>
                <div style="height:350px">
                    <canvas id="featureChart"></canvas>
                </div>
            </div>
            <!-- Heatmap: Feature x Priority -->
            <div class="card p-6">
                <h2 class="text-lg font-semibold mb-4">Heatmap: Feature x Prioridade</h2>
                <div class="overflow-x-auto">
                    <table class="w-full text-sm">
                        <thead>
                            <tr class="text-zinc-400 border-b border-zinc-700">
                                <th class="text-left py-2 px-3">Feature</th>
                                <th class="text-center py-2 px-2">Highest</th>
                                <th class="text-center py-2 px-2">High</th>
                                <th class="text-center py-2 px-2">Medium</th>
                                <th class="text-center py-2 px-2">Low</th>
                            </tr>
                        </thead>
                        <tbody id="heatmapBody"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Open Bugs Table -->
        <div class="card p-6 mb-8">
            <div class="flex items-center justify-between mb-4">
                <h2 class="text-lg font-semibold">Bugs Abertos ({m['open']})</h2>
                <div class="flex gap-2">
                    <input type="text" id="searchBugs" placeholder="Filtrar..." class="bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-zinc-500" />
                    <select id="filterPriority" class="bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm focus:outline-none">
                        <option value="">Todas prioridades</option>
                        <option value="Highest">Highest</option>
                        <option value="High">High</option>
                        <option value="Medium">Medium</option>
                        <option value="Low">Low</option>
                    </select>
                </div>
            </div>
            <div class="overflow-x-auto">
                <table class="w-full" id="bugsTable">
                    <thead>
                        <tr class="text-zinc-400 text-sm border-b border-zinc-700">
                            <th class="text-left py-2 px-4">Key</th>
                            <th class="text-left py-2 px-4">Titulo</th>
                            <th class="text-left py-2 px-4">Feature</th>
                            <th class="text-left py-2 px-4">Prioridade</th>
                            <th class="text-left py-2 px-4">SP</th>
                            <th class="text-left py-2 px-4">Status</th>
                            <th class="text-left py-2 px-4">Responsavel</th>
                            <th class="text-left py-2 px-4">Idade</th>
                        </tr>
                    </thead>
                    <tbody>
                        {open_rows}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Alerts -->
        {f'<div class="flex flex-col gap-2 mb-8">{alerts_html}</div>' if alerts_html else ""}

        <!-- Footer -->
        <div class="text-center text-zinc-600 text-xs py-8">
            Claint QA Dashboard &middot; Dados do Jira em tempo de geracao &middot; Rerun <code>python generate-dashboard.py</code> para atualizar
        </div>
    </div>

    <script>
    // --- Chart.js defaults ---
    Chart.defaults.color = '#a1a1aa';
    Chart.defaults.borderColor = '#27272a';
    Chart.defaults.font.family = 'system-ui';

    // --- Status Donut ---
    new Chart(document.getElementById('statusChart'), {{
        type: 'doughnut',
        data: {{
            labels: {json.dumps(status_labels, ensure_ascii=False)},
            datasets: [{{
                data: {json.dumps(status_values)},
                backgroundColor: {json.dumps([status_colors_map.get(s, '#64748b') for s in status_labels])},
                borderWidth: 0,
                hoverOffset: 8
            }}]
        }},
        options: {{
            cutout: '60%',
            plugins: {{
                legend: {{ position: 'bottom', labels: {{ padding: 12, usePointStyle: true, pointStyle: 'circle' }} }}
            }}
        }}
    }});

    // --- Priority Bar (stacked: open + resolved) ---
    new Chart(document.getElementById('priorityChart'), {{
        type: 'bar',
        data: {{
            labels: {json.dumps(prio_labels)},
            datasets: [
                {{
                    label: 'Abertos',
                    data: {json.dumps(prio_open_values)},
                    backgroundColor: {json.dumps([prio_colors.get(p, '#64748b') for p in prio_labels])},
                    borderRadius: 4,
                    barThickness: 32
                }},
                {{
                    label: 'Resolvidos',
                    data: {json.dumps(prio_resolved_values)},
                    backgroundColor: {json.dumps([prio_colors_dim.get(p, '#334155') for p in prio_labels])},
                    borderRadius: 4,
                    barThickness: 32
                }}
            ]
        }},
        options: {{
            indexAxis: 'y',
            plugins: {{ legend: {{ position: 'bottom', labels: {{ usePointStyle: true, pointStyle: 'circle', padding: 12 }} }} }},
            scales: {{
                x: {{ stacked: true, grid: {{ display: false }} }},
                y: {{ stacked: true, grid: {{ display: false }} }}
            }}
        }}
    }});

    // --- Feature Stacked Bar ---
    new Chart(document.getElementById('featureChart'), {{
        type: 'bar',
        data: {{
            labels: {json.dumps(feature_names, ensure_ascii=False)},
            datasets: [
                {{
                    label: 'Resolvidos',
                    data: {json.dumps(feature_resolved)},
                    backgroundColor: '#22c55e',
                    borderRadius: 4
                }},
                {{
                    label: 'Abertos',
                    data: {json.dumps(feature_open)},
                    backgroundColor: '#f97316',
                    borderRadius: 4
                }}
            ]
        }},
        options: {{
            indexAxis: 'y',
            plugins: {{ legend: {{ position: 'bottom', labels: {{ usePointStyle: true, pointStyle: 'circle' }} }} }},
            scales: {{
                x: {{ stacked: true, grid: {{ display: false }} }},
                y: {{ stacked: true, grid: {{ display: false }} }}
            }}
        }}
    }});

    // --- Heatmap Table ---
    const allBugs = {all_bugs_json};
    const features = [...new Set(allBugs.map(b => b.epic_name || 'Sem Epic'))];
    const priorities = ['Highest', 'High', 'Medium', 'Low'];
    const heatBody = document.getElementById('heatmapBody');

    features.sort((a, b) => {{
        const aTotal = allBugs.filter(bug => (bug.epic_name || 'Sem Epic') === a).length;
        const bTotal = allBugs.filter(bug => (bug.epic_name || 'Sem Epic') === b).length;
        return bTotal - aTotal;
    }});

    features.forEach(f => {{
        const row = document.createElement('tr');
        row.className = 'border-b border-zinc-800';
        let cells = `<td class="py-2 px-3 text-sm">${{f}}</td>`;
        priorities.forEach(p => {{
            const count = allBugs.filter(b => (b.epic_name || 'Sem Epic') === f && b.priority === p).length;
            const openCount = allBugs.filter(b => (b.epic_name || 'Sem Epic') === f && b.priority === p && b.resolution === 'Unresolved').length;
            let bg = '';
            if (openCount > 0 && (p === 'Highest' || p === 'High')) bg = 'background: rgba(239,68,68,0.15)';
            else if (count > 3) bg = 'background: rgba(234,179,8,0.1)';
            const display = count > 0 ? (openCount > 0 ? `${{count}} <span class="text-red-400 text-xs">(${{openCount}})</span>` : `${{count}}`) : '-';
            cells += `<td class="text-center py-2 px-2 text-sm" style="${{bg}}">${{display}}</td>`;
        }});
        row.innerHTML = cells;
        heatBody.appendChild(row);
    }});

    // --- Table Filter ---
    const searchInput = document.getElementById('searchBugs');
    const filterPrio = document.getElementById('filterPriority');
    const tableRows = document.querySelectorAll('#bugsTable tbody tr');

    function filterTable() {{
        const search = searchInput.value.toLowerCase();
        const prio = filterPrio.value;
        tableRows.forEach(row => {{
            const text = row.textContent.toLowerCase();
            const prioText = row.querySelector('td:nth-child(4)')?.textContent.trim() || '';
            const matchSearch = !search || text.includes(search);
            const matchPrio = !prio || prioText.includes(prio);
            row.style.display = matchSearch && matchPrio ? '' : 'none';
        }});
    }}

    searchInput.addEventListener('input', filterTable);
    filterPrio.addEventListener('change', filterTable);
    </script>
</body>
</html>"""
    return html

# --- Main ---
if __name__ == "__main__":
    if "--fetch" in sys.argv:
        print("Buscando bugs do Jira via curl...")
        raw = fetch_all_bugs()
    elif RAW_JSON.exists():
        print(f"Carregando dados de {RAW_JSON}...")
        raw = load_from_file()
    else:
        print("Arquivo bugs-raw.json nao encontrado. Use --fetch para buscar do Jira.")
        sys.exit(1)
    print(f"  {len(raw)} bugs encontrados")

    print("Processando metricas...")
    bugs = process_bugs(raw)
    metrics = compute_metrics(bugs)

    print("Gerando dashboard HTML...")
    html = generate_html(metrics)
    OUTPUT.write_text(html, encoding="utf-8")
    print(f"Dashboard salvo em: {OUTPUT}")
    print(f"  Total: {metrics['total']} bugs")
    print(f"  Resolvidos: {metrics['resolved']} ({metrics['completion_pct']}%)")
    print(f"  Abertos: {metrics['open']}")
    print(f"  Criticos abertos: {metrics['critical_open_count']}")
