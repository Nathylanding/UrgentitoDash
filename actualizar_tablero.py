import datetime as dt
import json
import os
import pandas as pd
import requests

GRAPH_VERSION = "v22.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"


def normalize_account_id(raw_id: str) -> str:
    clean = (raw_id or "").strip()
    if not clean:
        return ""
    return clean if clean.startswith("act_") else f"act_{clean}"


def extract_leads(actions):
    if not isinstance(actions, list):
        return 0.0
    total = 0.0
    for item in actions:
        action_type = item.get("action_type", "")
        if action_type in {"onsite_conversion.lead_grouped", "lead"}:
            try:
                total += float(item.get("value", 0) or 0)
            except (ValueError, TypeError):
                pass
    return total


def fetch_campaigns_budget(act_id: str, token: str) -> dict:
    url = f"{BASE_URL}/{act_id}/campaigns"
    params = {
        "fields": "id,name,status,daily_budget,lifetime_budget",
        "limit": 200,
        "access_token": token,
    }
    budgets = {}
    while url:
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        for c in payload.get("data", []):
            daily_raw = c.get("daily_budget")
            lifetime_raw = c.get("lifetime_budget")
            daily_val = float(daily_raw) / 100.0 if daily_raw is not None else 0.0
            lifetime_val = float(lifetime_raw) / 100.0 if lifetime_raw is not None else 0.0
            budgets[c.get("id", "")] = {
                "status": c.get("status", ""),
                "daily_budget": daily_val,
                "lifetime_budget": lifetime_val,
            }
        paging = payload.get("paging", {})
        url = paging.get("next")
        params = None
    return budgets


def fetch_daily_insights(act_id: str, token: str, since_str: str, until_str: str) -> list:
    url = f"{BASE_URL}/{act_id}/insights"
    params = {
        "level": "campaign",
        "time_increment": 1,
        "time_range": json.dumps({"since": since_str, "until": until_str}),
        "fields": "campaign_id,campaign_name,date_start,spend,actions",
        "limit": 500,
        "access_token": token,
    }
    rows = []
    while url:
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        rows.extend(payload.get("data", []))
        paging = payload.get("paging", {})
        url = paging.get("next")
        params = None
    return rows


def process_account(marca: str, account_id_env: str, token_env: str, since_str: str, until_str: str) -> list:
    raw_id = os.environ.get(account_id_env, "").strip()
    token = os.environ.get(token_env, "").strip()
    if not raw_id or not token:
        print(f"Advertencia: faltan credenciales para {marca} ({account_id_env} / {token_env}).")
        return []

    act_id = normalize_account_id(raw_id)
    budgets = fetch_campaigns_budget(act_id, token)
    insights = fetch_daily_insights(act_id, token, since_str, until_str)

    records = []
    for row in insights:
        cid = row.get("campaign_id", "")
        b_info = budgets.get(cid, {})
        spend = float(row.get("spend", 0) or 0)
        leads = extract_leads(row.get("actions"))
        records.append(
            {
                "marca": marca,
                "account_id": act_id,
                "campaign_id": cid,
                "campaign_name": row.get("campaign_name", "Sin nombre"),
                "status": b_info.get("status", "UNKNOWN"),
                "daily_budget": b_info.get("daily_budget", 0.0),
                "date": row.get("date_start", ""),
                "spend": round(spend, 2),
                "leads": round(leads, 2),
            }
        )
    return records


def main():
    hoy_quito = dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date()
    primer_dia_mes_actual = hoy_quito.replace(day=1)
    ultimo_dia_mes_anterior = primer_dia_mes_actual - dt.timedelta(days=1)
    primer_dia_mes_anterior = ultimo_dia_mes_anterior.replace(day=1)

    since_str = primer_dia_mes_anterior.strftime("%Y-%m-%d")
    until_str = hoy_quito.strftime("%Y-%m-%d")

    all_rows = []
    all_rows.extend(
        process_account(
            "Urgentito Talleres",
            "URGENTITO_TALLERES_ACCOUNT_ID",
            "URGENTITO_TALLERES_ACCESS_TOKEN",
            since_str,
            until_str,
        )
    )
    all_rows.extend(
        process_account(
            "Urgentito Ambato",
            "URGENTITO_AMBATO_ACCOUNT_ID",
            "URGENTITO_AMBATO_ACCESS_TOKEN",
            since_str,
            until_str,
        )
    )

    if all_rows:
        df = pd.DataFrame(all_rows)
        df = df.sort_values(["date", "marca", "campaign_name"])
        registros = df.to_dict(orient="records")
    else:
        registros = []

    output = {
        "metadata": {
            "fecha_referencia": hoy_quito.strftime("%Y-%m-%d"),
            "since": since_str,
            "until": until_str,
            "generado_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "cuentas": ["Urgentito Talleres", "Urgentito Ambato"],
        },
        "registros": registros,
    }

    with open("tablero_leads_urgentito.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Generado tablero_leads_urgentito.json con {len(registros)} registros ({since_str} a {until_str}).")


if __name__ == "__main__":
    main()
