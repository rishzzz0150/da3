
import os
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlparse, unquote, parse_qs

import pg8000.dbapi
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)


# =========================================================
# DATABASE CONNECTION
# =========================================================

def get_connection():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set in this terminal.")

    parsed = urlparse(url)
    return pg8000.dbapi.connect(
        host=parsed.hostname,
        port=parsed.port or 26257,
        database=unquote(parsed.path.lstrip("/")),
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        ssl_context=True,
        timeout=15,
    )


def clean(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def result_dicts(cursor):
    names = [c[0] for c in cursor.description] if cursor.description else []
    return [
        {names[i]: clean(value) for i, value in enumerate(row)}
        for row in cursor.fetchall()
    ]


def query(sql, params=()):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = result_dicts(cur)
        conn.commit()
        return rows
    finally:
        conn.close()


def ident(name):
    """Quote a validated database identifier."""
    if not isinstance(name, str) or not name or not name.replace("_", "").isalnum():
        raise ValueError("Invalid database identifier")
    return '"' + name.replace('"', '""') + '"'


# =========================================================
# SCHEMA DISCOVERY
# =========================================================

def get_tables():
    return query("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)


def table_names():
    return {r["table_name"] for r in get_tables()}


def get_columns(table):
    if table not in table_names():
        raise ValueError("Unknown table")

    return query("""
        SELECT column_name, data_type, is_nullable, column_default,
               ordinal_position
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """, (table,))


def column_names(table):
    return {c["column_name"] for c in get_columns(table)}


def primary_keys(table):
    return query("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
         AND tc.table_name = kcu.table_name
        WHERE tc.table_schema = 'public'
          AND tc.table_name = %s
          AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.ordinal_position
    """, (table,))


def foreign_keys(table):
    return query("""
        SELECT kcu.column_name,
               ccu.table_name AS referenced_table,
               ccu.column_name AS referenced_column
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.constraint_schema = tc.constraint_schema
        WHERE tc.table_schema = 'public'
          AND tc.table_name = %s
          AND tc.constraint_type = 'FOREIGN KEY'
    """, (table,))


def existing(*names):
    available = table_names()
    return next((name for name in names if name in available), None)


def choose(columns, *candidates):
    return next((c for c in candidates if c in columns), None)


def count_rows(table):
    if not table or table not in table_names():
        return 0
    rows = query(f"SELECT COUNT(*) AS n FROM {ident(table)}")
    return int(rows[0]["n"]) if rows else 0


# =========================================================
# DASHBOARD ANALYTICS
# =========================================================

def dashboard_data():
    available = table_names()
    teams_table = existing("teams")
    matches_table = existing("matches")
    games_table = existing("games")
    tournaments_table = existing("tournaments")
    stages_table = existing("tournament_stages")
    prizes_table = existing("prize_pools")

    metrics = {
        "active_tournaments": 0,
        "upcoming_matches": 0,
        "completed_matches": 0,
        "total_matches": 0,
        "completion_rate": 0,
        "registered_teams": count_rows(teams_table),
        "total_prize_pool": 0,
        "games_tracked": count_rows(games_table),
    }

    team_performance = []
    game_activity = []
    match_outcomes = []
    upcoming = []
    recent = []
    leaderboard = []

    # Active tournaments: only use a known status column.
    if tournaments_table:
        cols = column_names(tournaments_table)
        status = choose(cols, "status", "tournament_status")
        if status:
            rows = query(f"""
                SELECT COUNT(*) AS n
                FROM {ident(tournaments_table)}
                WHERE LOWER(CAST({ident(status)} AS STRING))
                  IN ('active', 'ongoing', 'in progress', 'live')
            """)
            metrics["active_tournaments"] = int(rows[0]["n"])

    # Prize pool aggregate.
    if prizes_table:
        cols = column_names(prizes_table)
        amount = choose(
            cols, "amount", "prize_amount", "total_amount",
            "pool_amount", "prize_pool_amount"
        )
        if amount:
            rows = query(
                f"SELECT COALESCE(SUM({ident(amount)}), 0) AS total "
                f"FROM {ident(prizes_table)}"
            )
            metrics["total_prize_pool"] = rows[0]["total"]

    if matches_table:
        mc = column_names(matches_table)
        mt = ident(matches_table)
        date_col = choose(mc, "match_date", "scheduled_at", "start_time")
        status_col = choose(mc, "status", "match_status")
        winner_col = choose(mc, "winner_team_id", "winner_id", "winning_team_id")
        team1_col = choose(mc, "team1_id", "team_1_id", "team1_team_id")
        team2_col = choose(mc, "team2_id", "team_2_id", "team2_team_id")

        metrics["total_matches"] = count_rows(matches_table)

        if status_col:
            rows = query(f"""
                SELECT COUNT(*) AS n FROM {mt}
                WHERE LOWER(CAST({ident(status_col)} AS STRING))
                  IN ('completed', 'finished', 'played')
            """)
            metrics["completed_matches"] = int(rows[0]["n"])
        elif winner_col:
            rows = query(
                f"SELECT COUNT(*) AS n FROM {mt} "
                f"WHERE {ident(winner_col)} IS NOT NULL"
            )
            metrics["completed_matches"] = int(rows[0]["n"])

        if metrics["total_matches"]:
            metrics["completion_rate"] = round(
                metrics["completed_matches"] * 100 / metrics["total_matches"], 1
            )

        # Pie chart categories: completed vs scheduled vs unresolved.
        total = metrics["total_matches"]
        completed = metrics["completed_matches"]
        if status_col:
            outcome_rows = query(f"""
                SELECT
                  CASE
                    WHEN LOWER(CAST({ident(status_col)} AS STRING))
                      IN ('completed', 'finished', 'played')
                      THEN 'Completed'
                    WHEN LOWER(CAST({ident(status_col)} AS STRING))
                      IN ('cancelled', 'canceled')
                      THEN 'Cancelled'
                    WHEN LOWER(CAST({ident(status_col)} AS STRING))
                      IN ('scheduled', 'upcoming', 'pending')
                      THEN 'Scheduled'
                    ELSE 'Other / unresolved'
                  END AS outcome,
                  COUNT(*) AS total
                FROM {mt}
                GROUP BY outcome
                ORDER BY total DESC
            """)
            match_outcomes = outcome_rows
        elif winner_col:
            unresolved = max(0, total - completed)
            match_outcomes = [
                {"outcome": "Completed", "total": completed},
                {"outcome": "No recorded winner", "total": unresolved},
            ]
        else:
            match_outcomes = [
                {"outcome": "Matches in database", "total": total}
            ]

        # Upcoming fixtures.
        if date_col:
            conditions = [f"{ident(date_col)} >= CURRENT_TIMESTAMP"]
            if status_col:
                conditions.append(
                    f"LOWER(CAST({ident(status_col)} AS STRING)) "
                    "NOT IN ('completed', 'finished', 'played', 'cancelled', 'canceled')"
                )
            elif winner_col:
                conditions.append(f"{ident(winner_col)} IS NULL")

            upcoming = query(
                f"SELECT * FROM {mt} WHERE "
                + " AND ".join(conditions)
                + f" ORDER BY {ident(date_col)} LIMIT 8"
            )
            metrics["upcoming_matches"] = len(upcoming)
            recent = query(
                f"SELECT * FROM {mt} ORDER BY {ident(date_col)} DESC LIMIT 8"
            )
        else:
            recent = query(f"SELECT * FROM {mt} LIMIT 8")

        # Wins and losses by team, only when team IDs are available.
        if teams_table and winner_col:
            tc = column_names(teams_table)
            pk = [x["column_name"] for x in primary_keys(teams_table)]
            tid = choose(tc, *(pk + ["team_id", "id"]))
            tname = choose(tc, "team_name", "name")
            if tid and tname:
                if team1_col and team2_col:
                    team_performance = query(f"""
                        SELECT t.{ident(tid)} AS team_id,
                               t.{ident(tname)} AS team_name,
                               SUM(CASE WHEN m.{ident(winner_col)} = t.{ident(tid)}
                                        THEN 1 ELSE 0 END) AS wins,
                               SUM(CASE WHEN m.{ident(winner_col)} IS NOT NULL
                                             AND (m.{ident(team1_col)} = t.{ident(tid)}
                                               OR m.{ident(team2_col)} = t.{ident(tid)})
                                             AND m.{ident(winner_col)} <> t.{ident(tid)}
                                        THEN 1 ELSE 0 END) AS losses
                        FROM {ident(teams_table)} t
                        LEFT JOIN {mt} m
                          ON m.{ident(team1_col)} = t.{ident(tid)}
                          OR m.{ident(team2_col)} = t.{ident(tid)}
                        GROUP BY t.{ident(tid)}, t.{ident(tname)}
                        ORDER BY wins DESC, losses ASC, team_name
                        LIMIT 10
                    """)
                else:
                    team_performance = query(f"""
                        SELECT t.{ident(tid)} AS team_id,
                               t.{ident(tname)} AS team_name,
                               COUNT(m.{ident(winner_col)}) AS wins,
                               0 AS losses
                        FROM {ident(teams_table)} t
                        LEFT JOIN {mt} m
                          ON m.{ident(winner_col)} = t.{ident(tid)}
                        GROUP BY t.{ident(tid)}, t.{ident(tname)}
                        ORDER BY wins DESC, team_name
                        LIMIT 10
                    """)

                leaderboard = team_performance[:8]

        # Game activity through tournaments, stages, and matches.
        if all([games_table, tournaments_table, stages_table]):
            gc = column_names(games_table)
            tc = column_names(tournaments_table)
            sc = column_names(stages_table)
            game_id = choose(gc, "game_id", "id")
            game_name = choose(gc, "game_name", "name")
            tgame = choose(tc, "game_id")
            tid = choose(tc, "tournament_id", "id")
            stid = choose(sc, "tournament_id")
            sid = choose(sc, "stage_id", "id")
            msid = choose(mc, "stage_id")

            if all([game_id, game_name, tgame, tid, stid, sid, msid]):
                game_activity = query(f"""
                    SELECT g.{ident(game_name)} AS game_name,
                           COUNT(m.*) AS matches
                    FROM {ident(games_table)} g
                    LEFT JOIN {ident(tournaments_table)} t
                      ON t.{ident(tgame)} = g.{ident(game_id)}
                    LEFT JOIN {ident(stages_table)} s
                      ON s.{ident(stid)} = t.{ident(tid)}
                    LEFT JOIN {mt} m
                      ON m.{ident(msid)} = s.{ident(sid)}
                    GROUP BY g.{ident(game_name)}
                    ORDER BY matches DESC, game_name
                    LIMIT 10
                """)

    return {
        "metrics": metrics,
        "team_performance": team_performance,
        "leaderboard": leaderboard,
        "game_activity": game_activity,
        "match_outcomes": match_outcomes,
        "upcoming_matches": upcoming,
        "recent_matches": recent,
        "tables": sorted(available),
    }


# =========================================================
# TABLE DATA / CRUD HELPERS
# =========================================================

def get_table_rows(table, limit=50, offset=0, search=""):
    cols = get_columns(table)
    names = [c["column_name"] for c in cols]
    sql = f"SELECT * FROM {ident(table)}"
    params = []

    if search.strip() and names:
        sql += " WHERE " + " OR ".join(
            f"CAST({ident(c)} AS STRING) ILIKE %s" for c in names
        )
        params = [f"%{search.strip()}%"] * len(names)

    keys = primary_keys(table)
    order_col = keys[0]["column_name"] if keys else (names[0] if names else None)
    if order_col:
        sql += f" ORDER BY {ident(order_col)}"

    sql += " LIMIT %s OFFSET %s"
    params.extend([limit, offset])

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        rows = result_dicts(cur)
        cur.execute(f"SELECT COUNT(*) FROM {ident(table)}")
        total = cur.fetchone()[0]
        return {"rows": rows, "total": total}
    finally:
        conn.close()


def payload_keys(table, payload):
    allowed = column_names(table)
    return {
        k: (None if v == "" else v)
        for k, v in payload.items()
        if k in allowed
    }


# =========================================================
# PAGES AND APIs
# =========================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    try:
        query("SELECT 1 AS connected")
        return jsonify({"ok": True, "message": "Connected"})
    except Exception:
        app.logger.exception("Health check failed")
        return jsonify({"ok": False, "message": "Database unavailable"}), 503


@app.route("/api/tables")
def tables_api():
    try:
        return jsonify(get_tables())
    except Exception:
        app.logger.exception("Table listing failed")
        return jsonify({"error": "Could not load tables"}), 500


@app.route("/api/schema/<table>")
def schema_api(table):
    try:
        return jsonify({
            "columns": get_columns(table),
            "primary_keys": primary_keys(table),
            "foreign_keys": foreign_keys(table),
        })
    except ValueError:
        return jsonify({"error": "Unknown table"}), 404
    except Exception:
        app.logger.exception("Schema request failed")
        return jsonify({"error": "Could not load schema"}), 500


@app.route("/api/dashboard")
def dashboard_api():
    try:
        return jsonify(dashboard_data())
    except Exception:
        app.logger.exception("Dashboard query failed")
        return jsonify({"error": "Dashboard analytics failed. Check the terminal traceback."}), 500


@app.route("/api/report-options")
def report_options():
    try:
        return jsonify([
            {
                "table": item["table_name"],
                "columns": [
                    {"name": c["column_name"], "type": c["data_type"]}
                    for c in get_columns(item["table_name"])
                ],
            }
            for item in get_tables()
        ])
    except Exception:
        app.logger.exception("Report options failed")
        return jsonify({"error": "Could not load report options"}), 500


@app.route("/api/custom-report", methods=["POST"])
def custom_report():
    try:
        p = request.get_json(silent=True) or {}
        table = p.get("table", "")
        available_tables = table_names()
        if table not in available_tables:
            return jsonify({"error": "Choose a valid table"}), 400

        schema = get_columns(table)
        schema_map = {c["column_name"]: c for c in schema}
        selected = p.get("columns", [])
        if not selected or any(c not in schema_map for c in selected):
            return jsonify({"error": "Select valid columns"}), 400

        aggregate = p.get("aggregate", "none")
        allowed_aggregates = {"none", "count", "sum", "avg", "min", "max"}
        if aggregate not in allowed_aggregates:
            return jsonify({"error": "Invalid calculation"}), 400

        aggregate_col = p.get("aggregate_column", "")
        group_by = p.get("group_by", "")
        sort_by = p.get("sort_by", "")
        sort_order = p.get("sort_order", "DESC").upper()
        if sort_order not in {"ASC", "DESC"}:
            sort_order = "DESC"

        if group_by and group_by not in schema_map:
            return jsonify({"error": "Invalid grouping column"}), 400
        if aggregate not in {"none", "count"} and aggregate_col not in schema_map:
            return jsonify({"error": "Choose a calculation column"}), 400
        if sort_by and sort_by not in schema_map and not (
            aggregate != "none" and sort_by == "report_value"
        ):
            return jsonify({"error": "Invalid sort column"}), 400

        if aggregate in {"sum", "avg"}:
            numeric = {
                "integer", "bigint", "smallint", "numeric", "decimal",
                "real", "double precision", "int2", "int4", "int8",
                "float4", "float8"
            }
            if schema_map[aggregate_col]["data_type"].lower() not in numeric:
                return jsonify({"error": "SUM and AVG need a numeric column"}), 400

        where = []
        params = []
        operators = {
            "=", "!=", ">", ">=", "<", "<=", "contains", "is_null", "is_not_null"
        }

        for f in p.get("filters", [])[:10]:
            col = f.get("column", "")
            op = f.get("operator", "=")
            value = f.get("value")
            if col not in schema_map or op not in operators:
                return jsonify({"error": "Invalid filter"}), 400

            qcol = ident(col)
            if op == "is_null":
                where.append(f"{qcol} IS NULL")
            elif op == "is_not_null":
                where.append(f"{qcol} IS NOT NULL")
            elif op == "contains":
                where.append(f"CAST({qcol} AS STRING) ILIKE %s")
                params.append("%" + str(value or "")[:200] + "%")
            else:
                if value is None or value == "":
                    return jsonify({"error": f"Enter a value for {col}"}), 400
                where.append(f"{qcol} {op} %s")
                params.append(value)

        qtable = ident(table)
        aggregate_sql = ""
        if aggregate == "count":
            aggregate_sql = "COUNT(*)"
        elif aggregate != "none":
            aggregate_sql = f"{aggregate.upper()}({ident(aggregate_col)})"

        if aggregate_sql:
            dimensions = list(dict.fromkeys(
                ([group_by] if group_by else []) +
                [c for c in selected if c != group_by]
            ))
            select_parts = [ident(c) for c in dimensions]
            select_parts.append(f"{aggregate_sql} AS report_value")
            sql = f"SELECT {', '.join(select_parts)} FROM {qtable}"
        else:
            sql = f"SELECT {', '.join(ident(c) for c in selected)} FROM {qtable}"

        if where:
            sql += " WHERE " + " AND ".join(where)
        if aggregate_sql and group_by:
            sql += f" GROUP BY {ident(group_by)}"

        if sort_by:
            if sort_by == "report_value" and aggregate_sql:
                sql += f" ORDER BY report_value {sort_order}"
            elif aggregate_sql and sort_by == group_by:
                sql += f" ORDER BY {ident(sort_by)} {sort_order}"
            elif not aggregate_sql:
                sql += f" ORDER BY {ident(sort_by)} {sort_order}"
            else:
                sql += f" ORDER BY report_value {sort_order}"
        elif aggregate_sql:
            sql += f" ORDER BY report_value {sort_order}"

        sql += " LIMIT 500"
        rows = query(sql, tuple(params))
        return jsonify({"rows": rows, "count": len(rows), "sql": sql})
    except Exception:
        app.logger.exception("Custom report failed")
        return jsonify({"error": "Could not generate report. Check selected fields and types."}), 400


@app.route("/api/data/<table>", methods=["GET"])
def data_api(table):
    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 500)
        offset = max(int(request.args.get("offset", 0)), 0)
        return jsonify(get_table_rows(
            table, limit, offset, request.args.get("search", "")
        ))
    except ValueError:
        return jsonify({"error": "Invalid table or pagination value"}), 400
    except Exception:
        app.logger.exception("Data read failed")
        return jsonify({"error": "Could not load table data"}), 500


@app.route("/api/data/<table>", methods=["POST"])
def create_row(table):
    conn = None
    try:
        cols = get_columns(table)
        keys = {c["column_name"] for c in cols}
        pk = {x["column_name"] for x in primary_keys(table)}
        body = request.get_json(silent=True) or {}
        values = {
            k: (None if v == "" else v)
            for k, v in body.items()
            if k in keys and not (k in pk and v in (None, ""))
        }

        conn = get_connection()
        cur = conn.cursor()
        if values:
            names = list(values)
            sql = (
                f"INSERT INTO {ident(table)} "
                f"({', '.join(ident(n) for n in names)}) VALUES "
                f"({', '.join(['%s'] * len(names))})"
            )
            cur.execute(sql, tuple(values[n] for n in names))
        else:
            cur.execute(f"INSERT INTO {ident(table)} DEFAULT VALUES")
        conn.commit()
        return jsonify({"ok": True}), 201
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception("Insert failed")
        return jsonify({"error": "Insert failed. Check required fields and foreign keys."}), 400
    finally:
        if conn:
            conn.close()


@app.route("/api/data/<table>", methods=["PUT"])
def update_row(table):
    conn = None
    try:
        allowed = column_names(table)
        pk = [x["column_name"] for x in primary_keys(table)]
        if not pk:
            return jsonify({"error": "Table has no primary key"}), 400

        body = request.get_json(silent=True) or {}
        key_values = body.get("_primary_key", {})
        if not key_values and len(pk) == 1 and "_id" in body:
            key_values = {pk[0]: body["_id"]}
        if any(k not in key_values for k in pk):
            return jsonify({"error": "Missing primary key"}), 400

        updates = {
            k: (None if v == "" else v)
            for k, v in body.items()
            if k in allowed and k not in pk
        }
        if not updates:
            return jsonify({"error": "No editable fields"}), 400

        sql = (
            f"UPDATE {ident(table)} SET " +
            ", ".join(f"{ident(k)} = %s" for k in updates) +
            " WHERE " + " AND ".join(f"{ident(k)} = %s" for k in pk)
        )
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            sql, tuple(updates.values()) + tuple(key_values[k] for k in pk)
        )
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "Record not found"}), 404
        conn.commit()
        return jsonify({"ok": True})
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception("Update failed")
        return jsonify({"error": "Update failed. Check values and relationships."}), 400
    finally:
        if conn:
            conn.close()


@app.route("/api/data/<table>", methods=["DELETE"])
def delete_row(table):
    conn = None
    try:
        pk = [x["column_name"] for x in primary_keys(table)]
        if not pk:
            return jsonify({"error": "Table has no primary key"}), 400

        body = request.get_json(silent=True) or {}
        keys = body.get("_primary_key", {})
        if not keys and len(pk) == 1 and "_id" in body:
            keys = {pk[0]: body["_id"]}
        if any(k not in keys for k in pk):
            return jsonify({"error": "Missing primary key"}), 400

        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            f"DELETE FROM {ident(table)} WHERE " +
            " AND ".join(f"{ident(k)} = %s" for k in pk),
            tuple(keys[k] for k in pk)
        )
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "Record not found"}), 404
        conn.commit()
        return jsonify({"ok": True})
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception("Delete failed")
        return jsonify({"error": "Delete failed. Other records may reference it."}), 400
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

