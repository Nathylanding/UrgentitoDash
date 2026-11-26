import calendar
import datetime as dt
import json
import os
import pandas as pd
import requests

GRAPH_VERSION = "v22.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"

ACCOUNTS = [
    {
        "brand": "Urgentito Talleres",
        "id_env": "URGENTITO_TALLERES_ACCOUNT_ID",
        "token_env": "URGENTITO_TALLERES_ACCESS_TOKEN",
    },
    {
        "brand": "Urgentito Ambato",
        "id_env": "URGENTITO_AMBATO_ACCOUNT_ID",
        "token_env": "URGENTITO_AMBATO_ACCESS_TOKEN",
    },
]

LEAD_TYPES = (
    "onsite_conversion.lead_grouped",
    "lead",
    "onsite_conversion.lead",
)

MSG_TYPES = (
    "onsite_conversion.messaging_conversation_started_7d",
    "onsite_conversion.messaging_conversation_started",
    "onsite_conversion.total_messaging_connection",
    "messaging_conversation_started_7d",
    "messaging_conversation_started",
)


def clean_id(raw: str) -> str:
    val = (raw or "").strip()
    return val[4:] if val.startswith("act_") else val


def extract_leads(actions, campaign_name: str = "") -> int:
    if not isinstance(actions, list):
        return 0

    by_type = {}
    for item in actions:
        atype = item.get("action_type")
        if not atype:
            continue
        try:
            val = float(item.get("value", 0) or 0)
        except (ValueError, TypeError):
            val = 0.0
        by_type[atype] = max(by_type.get(atype, 0.0), val)

    # Si el nombre de la campaña indica SMS / mensajes o no tiene leads de formulario,
    # revisamos primero las métricas de conversación que reporta Meta en "Resultados"
    name_upper = (campaign_name or "").upper()
    is_msg_campaign = "[SMS]" in name_upper or "MENSAJE" in name_upper or "WHATSAPP" in name_upper

    if is_msg_campaign:
        for mtype in MSG_TYPES:
            if mtype in by_type and by_type[mtype] > 0:
                return int(round(by_type[mtype]))

    for ltype in LEAD_TYPES:
        if ltype in by_type and by_type[ltype] > 0:
            return int(round(by_type[ltype]))

    for mtype in MSG_TYPES:
        if mtype in by_type and by_type[mtype] > 0:
            return int(round(by_type[mtype]))

    return 0


def calc_var(curr: float, prev: float, decimals: int = 2) -> dict:
    diff = round(curr - prev, decimals)
    if prev and prev > 0:
        pct = round(((curr - prev) / prev) * 100.0, 1)
    else:
        pct = None
    return {"abs": diff, "pct": pct}


def prev_month_key(ym: str) -> str:
    y, m = map(int, ym.split("-"))
    if m == 1:
        return f"{y - 1}-12"
    return f"{y}-{m - 1:02d}"


def same_day_prev_month(date_str: str) -> str:
    y, m, d = map(int, date_str.split("-"))
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    max_d = calendar.monthrange(py, pm)[1]
    return f"{py}-{pm:02d}-{min(d, max_d):02d}"


def fetch_campaigns_budget(act_id: str, token: str) -> dict:
    url = f"{BASE_URL}/act_{act_id}/campaigns"
    params = {
        "fields": "id,name,status,daily_budget",
        "limit": 200,
        "access_token": token,
    }
    budgets = {}
    try:
        while url:
            r = requests.get(url, params=params, timeout=60)
            if r.status_code >= 400:
                print(f"Aviso /campaigns en act_{act_id}: {r.status_code} - {r.text}")
                return {}
            data = r.json()
            for c in data.get("data", []):
                db_raw = c.get("daily_budget")
                db = float(db_raw) / 100.0 if db_raw not in (None, "") else 0.0
                budgets[c["id"]] = {
                    "status": c.get("status", "UNKNOWN"),
                    "daily_budget": round(db, 2),
                }
            url = data.get("paging", {}).get("next")
            params = None
    except Exception as exc:
        print(f"Aviso leyendo campañas en act_{act_id}: {exc}")
    return budgets


def fetch_daily_insights(act_id: str, token: str, since_str: str, until_str: str) -> list:
    url = f"{BASE_URL}/act_{act_id}/insights"
    params = {
        "level": "campaign",
        "time_increment": 1,
        "time_range": json.dumps({"since": since_str, "until": until_str}),
        "fields": "campaign_id,campaign_name,date_start,spend,impressions,clicks,actions",
        "limit": 500,
        "access_token": token,
    }
    rows = []
    while url:
        r = requests.get(url, params=params, timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(f"Error /insights act_{act_id} ({r.status_code}): {r.text}")
        data = r.json()
        rows.extend(data.get("data", []))
        url = data.get("paging", {}).get("next")
        params = None
    return rows


def main():
    hoy_quito = dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date()
    since_str = "2026-08-01"
    until_str = hoy_quito.strftime("%Y-%m-%d")
    current_ym = hoy_quito.strftime("%Y-%m")
    current_day = hoy_quito.day

    accounts_ok = []
    accounts_failed = []
    raw_records = []

    for acc in ACCOUNTS:
        brand = acc["brand"]
        acc_id = clean_id(os.environ.get(acc["id_env"], ""))
        token = (os.environ.get(acc["token_env"]) or "").strip()
        if not acc_id or not token:
            accounts_failed.append({"brand": brand, "error": "Faltan variables de entorno"})
            continue
        try:
            budgets = fetch_campaigns_budget(acc_id, token)
            insights = fetch_daily_insights(acc_id, token, since_str, until_str)
            accounts_ok.append({"brand": brand, "account_id": acc_id})
            for row in insights:
                cid = row.get("campaign_id", "")
                cname = row.get("campaign_name", "Sin nombre")
                b = budgets.get(cid, {})
                raw_records.append(
                    {
                        "fecha": row.get("date_start", ""),
                        "ym": (row.get("date_start", ""))[:7],
                        "day": int((row.get("date_start", "0000-00-01"))[8:10]),
                        "brand": brand,
                        "campaign_id": cid,
                        "campaign_name": cname,
                        "status": b.get("status", "ACTIVE"),
                        "daily_budget": b.get("daily_budget", 0.0),
                        "spend": round(float(row.get("spend", 0) or 0), 2),
                        "leads": extract_leads(row.get("actions"), cname),
                        "impressions": int(row.get("impressions", 0) or 0),
                        "clicks": int(row.get("clicks", 0) or 0),
                    }
                )
        except Exception as exc:
            print(f"Error en {brand} ({acc_id}): {exc}")
            accounts_failed.append({"brand": brand, "account_id": acc_id, "error": str(exc)})

    scopes = ["Juntos", "Urgentito Talleres", "Urgentito Ambato"]
    all_dates = pd.date_range(since_str, until_str, freq="D").strftime("%Y-%m-%d").tolist()
    all_months = sorted({d[:7] for d in all_dates})
    meses_disponibles = [m for m in all_months if m > "2026-08"]

    df = pd.DataFrame(raw_records) if raw_records else pd.DataFrame(
        columns=[
            "fecha", "ym", "day", "brand", "campaign_id", "campaign_name",
            "status", "daily_budget", "spend", "leads", "impressions", "clicks"
        ]
    )

    serie_diaria_leads = []
    lookup_daily = {}

    for sc in scopes:
        sub = df if sc == "Juntos" else df[df["brand"] == sc]
        grp = (
            sub.groupby("fecha", as_index=False)[["leads", "spend", "impressions", "clicks"]].sum()
            if not sub.empty
            else pd.DataFrame(columns=["fecha", "leads", "spend", "impressions", "clicks"])
        )
        daily_map = grp.set_index("fecha").to_dict(orient="index")

        rolling_window = []
        for f in all_dates:
            vals = daily_map.get(f, {"leads": 0, "spend": 0.0, "impressions": 0, "clicks": 0})
            leads = int(vals["leads"])
            spend = round(float(vals["spend"]), 2)
            impr = int(vals["impressions"])
            clicks = int(vals["clicks"])
            cpl = round(spend / leads, 2) if leads > 0 else 0.0

            rolling_window.append(leads)
            if len(rolling_window) > 7:
                rolling_window.pop(0)
            pm7 = round(sum(rolling_window) / len(rolling_window), 2)

            lookup_daily[(sc, f)] = {"leads": leads, "spend": spend}

            f_prev = same_day_prev_month(f)
            prev_vals = lookup_daily.get((sc, f_prev), {"leads": 0, "spend": 0.0})

            serie_diaria_leads.append(
                {
                    "fecha": f,
                    "scope": sc,
                    "leads": leads,
                    "spend": spend,
                    "cpl_diario": cpl,
                    "promedio_movil_7d": pm7,
                    "impressions": impr,
                    "clicks": clicks,
                    "fecha_mes_pasado": f_prev,
                    "leads_mes_pasado": prev_vals["leads"],
                    "var_leads_vs_mes_pasado": calc_var(leads, prev_vals["leads"], decimals=0),
                    "spend_mes_pasado": prev_vals["spend"],
                    "var_spend_vs_mes_pasado": calc_var(spend, prev_vals["spend"], decimals=2),
                }
            )

    comparativo_por_mes = {}
    for ym in meses_disponibles:
        prev_ym = prev_month_key(ym)
        y_m, m_m = map(int, ym.split("-"))
        dias_comp = current_day if ym == current_ym else calendar.monthrange(y_m, m_m)[1]

        comparativo_por_mes[ym] = {}
        for sc in scopes:
            sub = df if sc == "Juntos" else df[df["brand"] == sc]
            curr_df = sub[(sub["ym"] == ym) & (sub["day"] <= dias_comp)] if not sub.empty else sub
            prev_df = sub[(sub["ym"] == prev_ym) & (sub["day"] <= dias_comp)] if not sub.empty else sub

            g_act = round(float(curr_df["spend"].sum()), 2) if not curr_df.empty else 0.0
            g_ant = round(float(prev_df["spend"].sum()), 2) if not prev_df.empty else 0.0
            l_act = int(curr_df["leads"].sum()) if not curr_df.empty else 0
            l_ant = int(prev_df["leads"].sum()) if not prev_df.empty else 0
            cpl_act = round(g_act / l_act, 2) if l_act > 0 else 0.0
            cpl_ant = round(g_ant / l_ant, 2) if l_ant > 0 else 0.0

            comparativo_por_mes[ym][sc] = {
                "mes": ym,
                "mes_anterior": prev_ym,
                "dias_comparados": dias_comp,
                "gasto_actual": g_act,
                "gasto_mes_pasado": g_ant,
                "var_gasto": calc_var(g_act, g_ant, decimals=2),
                "leads_actual": l_act,
                "leads_mes_pasado": l_ant,
                "var_leads": calc_var(l_act, l_ant, decimals=0),
                "cpl_actual": cpl_act,
                "cpl_mes_pasado": cpl_ant,
                "var_cpl": calc_var(cpl_act, cpl_ant, decimals=2),
            }

    campanas_por_mes = {}
    for ym in meses_disponibles:
        prev_ym = prev_month_key(ym)
        y_m, m_m = map(int, ym.split("-"))
        dias_comp = current_day if ym == current_ym else calendar.monthrange(y_m, m_m)[1]

        curr_c = df[(df["ym"] == ym) & (df["day"] <= dias_comp)] if not df.empty else df
        prev_c = df[(df["ym"] == prev_ym) & (df["day"] <= dias_comp)] if not df.empty else df

        rows_camp = []
        if not curr_c.empty or not prev_c.empty:
            keys = set()
            if not curr_c.empty:
                keys.update(zip(curr_c["brand"], curr_c["campaign_name"]))
            if not prev_c.empty:
                keys.update(zip(prev_c["brand"], prev_c["campaign_name"]))

            for br, cname in sorted(keys):
                c_sub = (
                    curr_c[(curr_c["brand"] == br) & (curr_c["campaign_name"] == cname)]
                    if not curr_c.empty
                    else curr_c
                )
                p_sub = (
                    prev_c[(prev_c["brand"] == br) & (prev_c["campaign_name"] == cname)]
                    if not prev_c.empty
                    else prev_c
                )

                g_act = round(float(c_sub["spend"].sum()), 2) if not c_sub.empty else 0.0
                g_ant = round(float(p_sub["spend"].sum()), 2) if not p_sub.empty else 0.0
                l_act = int(c_sub["leads"].sum()) if not c_sub.empty else 0
                l_ant = int(p_sub["leads"].sum()) if not p_sub.empty else 0
                cpl_act = round(g_act / l_act, 2) if l_act > 0 else 0.0
                status = (
                    c_sub["status"].iloc[-1]
                    if not c_sub.empty
                    else (p_sub["status"].iloc[-1] if not p_sub.empty else "ACTIVE")
                )
                budget = (
                    float(c_sub["daily_budget"].max())
                    if not c_sub.empty
                    else (float(p_sub["daily_budget"].max()) if not p_sub.empty else 0.0)
                )

                rows_camp.append(
                    {
                        "brand": br,
                        "campaign_name": cname,
                        "status": status,
                        "daily_budget": round(budget, 2),
                        "gasto_actual": g_act,
                        "gasto_mes_pasado": g_ant,
                        "var_gasto": calc_var(g_act, g_ant, decimals=2),
                        "leads_actual": l_act,
                        "leads_mes_pasado": l_ant,
                        "var_leads": calc_var(l_act, l_ant, decimals=0),
                        "cpl_actual": cpl_act,
                    }
                )
        rows_camp.sort(key=lambda x: x["gasto_actual"], reverse=True)
        campanas_por_mes[ym] = rows_camp

    output = {
        "metadata": {
            "since": since_str,
            "until": until_str,
            "fecha_referencia": until_str,
            "generated_at": until_str,
            "meses_disponibles": meses_disponibles,
            "scopes": scopes,
            "accounts_ok": accounts_ok,
            "accounts_failed": accounts_failed,
        },
        "comparativo_por_mes": comparativo_por_mes,
        "serie_diaria_leads": serie_diaria_leads,
        "campanas_por_mes": campanas_por_mes,
    }

    with open("tablero_leads_urgentito.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"JSON generado con {len(serie_diaria_leads)} puntos diarios y meses {meses_disponibles}.")


if __name__ == "__main__":
    main()
