from collections import defaultdict
from datetime import date
from statistics import mean
from typing import Any

import networkx as nx
from psycopg import sql

from app.config import Settings, get_settings
from app.database import read_connection
from app.models import GraphEdge, GraphNode, Metrics, NetworkGraph, Recommendation, RouteAnalysis, RouteOption, Segment, Summary

HUBS = ("Coimbatore", "Salem", "Trichy", "Chennai")


def rolling_year_start(end: date) -> date:
    try:
        return end.replace(year=end.year - 1)
    except ValueError:  # 29 February
        return end.replace(year=end.year - 1, day=28)


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None


def _status(metrics: Metrics, settings: Settings) -> tuple[str, str]:
    if metrics.trip_count == 0 or (metrics.load_utilization_pct is None and metrics.average_travel_hours is None):
        return "INSUFFICIENT DATA", "No contract trip has enough load/capacity or travel-time data to assess this leg."

    utilization, travel = metrics.load_utilization_pct, metrics.average_travel_hours
    poor_reasons: list[str] = []
    attention_reasons: list[str] = []
    if utilization is not None:
        if utilization > settings.maximum_utilization_pct:
            poor_reasons.append(f"load utilization is {utilization:.1f}%, above vehicle capacity")
        elif utilization < settings.attention_utilization_min_pct:
            poor_reasons.append(f"load utilization is {utilization:.1f}%")
        elif utilization < settings.good_utilization_min_pct:
            attention_reasons.append(f"load utilization is {utilization:.1f}%")
    if travel is not None:
        if travel > settings.poor_max_travel_hours:
            poor_reasons.append(f"average travel time is {travel:.1f} hours")
        elif travel > settings.attention_max_travel_hours:
            attention_reasons.append(f"average travel time is {travel:.1f} hours")

    if poor_reasons:
        return "POOR", "Needs review: " + " and ".join(poor_reasons) + "."
    if attention_reasons:
        return "NEEDS ATTENTION", "Review recommended: " + " and ".join(attention_reasons) + "."
    signals = []
    if utilization is not None:
        signals.append(f"load utilization is {utilization:.1f}%")
    if travel is not None:
        signals.append(f"average travel time is {travel:.1f} hours")
    return "GOOD", "Available operational metrics are within the configured bands (" + "; ".join(signals) + ")."


def _recommend(segments: list[Segment]) -> Recommendation:
    if not segments:
        return Recommendation(status="INSUFFICIENT DATA", reason="No historical Coimbatore-to-Chennai route combination was found for this analysis period.")
    order = {"POOR": 0, "NEEDS ATTENTION": 1, "INSUFFICIENT DATA": 2, "GOOD": 3}
    worst = min(segments, key=lambda item: order[item.status])
    if worst.status == "INSUFFICIENT DATA":
        return Recommendation(status="INSUFFICIENT DATA", reason="The route cannot be fully assessed because one or more legs lack usable load/capacity or travel-time data.")
    route_status = {"POOR": "POOR ROUTE", "NEEDS ATTENTION": "NEEDS ATTENTION", "GOOD": "GOOD ROUTE"}[worst.status]
    return Recommendation(status=route_status, reason=f"{worst.source} to {worst.destination} has the weakest leg result: {worst.reason}")


def _aggregate(rows: list[dict[str, Any]], trip_rows: list[dict[str, Any]], settings: Settings) -> tuple[Metrics, str, str]:
    loads = [r["load"] for r in rows if r["load"] is not None]
    capacities = [r["capacity"] for r in rows if r["capacity"] is not None]
    load_capacity_pairs = [r for r in rows if r["load"] is not None and r["capacity"] is not None and r["capacity"] > 0]
    durations = [r["travel_hours"] for r in rows if r["travel_hours"] is not None and r["travel_hours"] > 0]
    prices = [r["price"] for r in rows if r["price"] is not None]
    total_load = sum(loads) if loads else None
    total_paired_load = sum(r["load"] for r in load_capacity_pairs)
    total_paired_capacity = sum(r["capacity"] for r in load_capacity_pairs)
    utilization = (100 * total_paired_load * settings.load_to_capacity_factor / total_paired_capacity) if total_paired_capacity and settings.load_to_capacity_factor is not None else None
    metrics = Metrics(
        trip_count=len(trip_rows),
        total_load=total_load,
        average_load=mean(loads) if loads else None,
        average_capacity=mean(capacities) if capacities else None,
        load_utilization_pct=utilization,
        average_travel_hours=mean(durations) if durations else None,
        minimum_travel_hours=min(durations) if durations else None,
        maximum_travel_hours=max(durations) if durations else None,
        travel_time_trip_count=len(durations),
        earliest_departure=min((r["departure"] for r in rows if r.get("departure") is not None), default=None),
        latest_arrival=max((r["arrival"] for r in rows if r.get("arrival") is not None), default=None),
        average_contract_vehicle_price=mean(prices) if prices else None,
        minimum_contract_vehicle_price=min(prices) if prices else None,
        maximum_contract_vehicle_price=max(prices) if prices else None,
        contract_price_trip_count=len(prices),
    )
    return metrics, *_status(metrics, settings)


def analyze(start: date, end: date) -> RouteAnalysis:
    settings = get_settings()
    codes = settings.station_codes
    city_by_code = dict(zip(codes, HUBS))
    route_table = sql.Identifier(settings.db_schema, "route")
    header_table = sql.Identifier(settings.db_schema, "tbl_despatch_header")
    contract_table = sql.Identifier(settings.db_schema, "tbl_contvehent")
    truck_table = sql.Identifier(settings.db_schema, "tbl_tss_truck")
    detail_table = sql.Identifier(settings.db_schema, "tbl_despatch_detl")
    waybill_table = sql.Identifier(settings.db_schema, "wbhead")
    events_table = sql.Identifier(settings.db_schema, "tbl_veharrival_despatch")
    # Read direct links among the configured hubs. Contract trips in the
    # requested analysis window determine which links and paths are active.
    route_query = sql.SQL("""
        SELECT DISTINCT route, start, stop FROM {route}
        WHERE type = 'R' AND upper(trim(via)) = 'DIR'
          AND start = ANY(%s) AND stop = ANY(%s) AND start <> stop
    """).format(route=route_table)
    trip_query = sql.SQL("""
        SELECT DISTINCT d.tssno, upper(trim(d.vehicleno)) AS vehicle_no,
               d.tssdate::date AS trip_date, d.route AS route_code,
               ct.tothire::double precision AS contract_price
        FROM {header} d
        JOIN {contract} ct ON upper(trim(ct.tssno)) = upper(trim(d.tssno))
        WHERE d.tssdate >= %s::date AND d.tssdate < (%s::date + interval '1 day')
          AND d.route = ANY(%s) AND d.tssno IS NOT NULL
    """).format(header=header_table, contract=contract_table)

    with read_connection() as conn, conn.cursor() as cursor:
        cursor.execute(route_query, (list(codes), list(codes)))
        route_rows = cursor.fetchall()
        route_to_edge = {
            row["route"]: (row["start"], row["stop"])
            for row in route_rows
            if row["route"] and row["start"] in city_by_code and row["stop"] in city_by_code
        }
        route_codes = list(route_to_edge)
        if route_codes:
            cursor.execute(trip_query, (start, end, route_codes))
            trip_rows = [dict(row) for row in cursor.fetchall()]
        else:
            trip_rows = []
        active_codes = {row["route_code"] for row in trip_rows}
        used_route_to_edge = {code: pair for code, pair in route_to_edge.items() if code in active_codes}

        tss_numbers = list({row["tssno"] for row in trip_rows if row["tssno"]})
        vehicle_numbers = list({row["vehicle_no"] for row in trip_rows if row["vehicle_no"]})
        capacities: dict[str, list[tuple[date | None, float | None]]] = defaultdict(list)
        loads: dict[str, float] = {}
        movements: dict[str, tuple[Any, Any]] = {}

        if vehicle_numbers:
            capacity_query = sql.SQL("""
                SELECT upper(trim(regno)) AS vehicle_no, psdate::date AS effective_date,
                       capacity::double precision AS capacity
                FROM {truck} WHERE upper(trim(regno)) = ANY(%s)
                ORDER BY upper(trim(regno)), psdate
            """).format(truck=truck_table)
            cursor.execute(capacity_query, (vehicle_numbers,))
            for row in cursor.fetchall():
                capacities[row["vehicle_no"]].append((row["effective_date"], _number(row["capacity"])))

        if tss_numbers:
            load_query = sql.SQL("""
                SELECT tssno, sum(chargewt)::double precision AS load
                FROM (
                    SELECT DISTINCT dd.tssno, dd.prefix, dd.wayno, w.chargewt
                    FROM {detail} dd
                    JOIN {waybill} w ON w.prefix = dd.prefix AND w.wayno = dd.wayno
                    WHERE dd.tssno = ANY(%s)
                ) dispatched_waybills
                GROUP BY tssno
            """).format(detail=detail_table, waybill=waybill_table)
            cursor.execute(load_query, (tss_numbers,))
            loads = {row["tssno"]: _number(row["load"]) for row in cursor.fetchall()}

            event_query = sql.SQL("""
                SELECT upper(trim(tssno)) AS tssno,
                       min(date + time) FILTER (WHERE lower(trim(type)) = 'despatch') AS departure,
                       max(date + time) FILTER (WHERE lower(trim(type)) = 'arrival') AS arrival
                FROM {events}
                WHERE tssno = ANY(%s) AND lower(trim(type)) IN ('despatch', 'arrival')
                GROUP BY upper(trim(tssno))
            """).format(events=events_table)
            cursor.execute(event_query, (tss_numbers,))
            movements = {row["tssno"]: (row["departure"], row["arrival"]) for row in cursor.fetchall()}

    rows_by_edge: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    trips_by_edge: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for trip in trip_rows:
        edge = used_route_to_edge.get(trip["route_code"])
        if edge is None:
            continue
        trips_by_edge[edge].append(trip)
        candidate_capacities = capacities.get(trip["vehicle_no"] or "", [])
        valid_capacity = [item for item in candidate_capacities if item[0] is not None and item[0] <= trip["trip_date"]]
        capacity = valid_capacity[-1][1] if valid_capacity else None
        departure, arrival = movements.get(trip["tssno"].strip().upper(), (None, None))
        travel_hours = (arrival - departure).total_seconds() / 3600 if departure and arrival and arrival > departure else None
        rows_by_edge[edge].append({
            "load": loads.get(trip["tssno"]),
            "capacity": capacity,
            "price": _number(trip["contract_price"]),
            "travel_hours": travel_hours,
            "departure": departure,
            "arrival": arrival,
        })

    graph = nx.DiGraph()
    graph.add_nodes_from(HUBS)
    segments_by_edge: dict[tuple[str, str], Segment] = {}
    all_rows: list[dict[str, Any]] = []
    used_edges = sorted(set(trips_by_edge), key=lambda pair: (HUBS.index(city_by_code[pair[0]]), HUBS.index(city_by_code[pair[1]])))
    for source_code, destination_code in used_edges:
        source, destination = city_by_code[source_code], city_by_code[destination_code]
        edge_key = (source_code, destination_code)
        trip_group = trips_by_edge[edge_key]
        metrics, status, reason = _aggregate(rows_by_edge[edge_key], trip_group, settings)
        graph.add_edge(source, destination, **metrics.model_dump(), status=status, reason=reason)
        edge = graph[source][destination]
        segments_by_edge[(source, destination)] = Segment(source=source, destination=destination, status=edge["status"], reason=edge["reason"], metrics=metrics)

    route_codes_graph = nx.DiGraph()
    route_codes_graph.add_nodes_from(codes)
    route_codes_graph.add_edges_from(used_edges)
    paths = sorted(
        nx.all_simple_paths(route_codes_graph, codes[0], codes[-1]),
        key=lambda path: (len(path), tuple(path)),
    ) if nx.has_path(route_codes_graph, codes[0], codes[-1]) else []
    path_edges = {(a, b) for path in paths for a, b in zip(path, path[1:])}
    used_edges = [edge for edge in used_edges if edge in path_edges]
    graph.remove_edges_from([
        (city_by_code[a], city_by_code[b])
        for a, b in route_codes_graph.edges
        if (a, b) not in path_edges
    ])
    segments_by_edge = {
        (city_by_code[a], city_by_code[b]): segments_by_edge[(city_by_code[a], city_by_code[b])]
        for a, b in path_edges
    }
    all_rows = [row for edge in used_edges for row in rows_by_edge[edge]]
    alternatives = []
    for path in paths:
        path_legs = [segments_by_edge[(city_by_code[a], city_by_code[b])] for a, b in zip(path, path[1:])]
        alternatives.append(RouteOption(
            cities=[city_by_code[code] for code in path],
            status=min(path_legs, key=lambda leg: {"POOR": 0, "NEEDS ATTENTION": 1, "INSUFFICIENT DATA": 2, "GOOD": 3}[leg.status]).status,
            legs=path_legs,
            average_full_route_travel_hours=(
                sum(leg.metrics.average_travel_hours for leg in path_legs)
                if all(leg.metrics.average_travel_hours is not None for leg in path_legs) else None
            ),
            average_full_route_contract_price=(
                sum(leg.metrics.average_contract_vehicle_price for leg in path_legs)
                if all(leg.metrics.average_contract_vehicle_price is not None for leg in path_legs) else None
            ),
        ))
    segments = [segments_by_edge[(city_by_code[a], city_by_code[b])] for a, b in used_edges]

    summary_loads = [r["load"] for r in all_rows if r["load"] is not None]
    summary_pairs = [r for r in all_rows if r["load"] is not None and r["capacity"] is not None and r["capacity"] > 0]
    summary_durations = [r["travel_hours"] for r in all_rows if r["travel_hours"] is not None and r["travel_hours"] > 0]
    summary_prices = [r["price"] for r in all_rows if r["price"] is not None]
    summary = Summary(
        trip_count=sum(len(trips_by_edge[edge]) for edge in used_edges),
        average_load=mean(summary_loads) if summary_loads else None,
        load_utilization_pct=(100 * sum(r["load"] for r in summary_pairs) * settings.load_to_capacity_factor / sum(r["capacity"] for r in summary_pairs)) if summary_pairs and settings.load_to_capacity_factor is not None else None,
        average_travel_hours=mean(summary_durations) if summary_durations else None,
        average_contract_vehicle_price=mean(summary_prices) if summary_prices else None,
    )
    notes = [
        "Load is summed from dispatched waybills joined to wbhead.chargewt; values retain the database's unspecified unit.",
        f"Load utilization converts charge-weight kilograms to capacity metric tons with LOAD_TO_CAPACITY_FACTOR={settings.load_to_capacity_factor}.",
        "Route combinations include only Coimbatore-to-Chennai paths whose direct legs have contract trips in the requested analysis window. Summary movement counts are leg movements; the database does not provide a through-route shipment identity.",
        "Full-route average time and contract cost are estimated by summing the average values for each leg; individual legs are not matched to the same shipment.",
        "Status bands are configurable defaults in .env.example and should be aligned with approved operating targets.",
        "Vehicle capacity uses the latest capacity record on or before each trip date; trips without a dated capacity record are excluded from utilization.",
    ]
    if not any(segment.metrics.travel_time_trip_count for segment in segments):
        notes.append("Travel time is Insufficient Data because matching dispatch and arrival event timestamps were unavailable.")
    graph_dto = NetworkGraph(
        nodes=[GraphNode(id=hub, label=hub) for hub in graph.nodes],
        edges=[
            GraphEdge(
                source=source,
                destination=destination,
                status=edge["status"],
                reason=edge["reason"],
                metrics=Metrics(**{field: edge[field] for field in Metrics.model_fields}),
            )
            for source, destination, edge in graph.edges(data=True)
        ],
    )
    return RouteAnalysis(route=list(HUBS), period={"start": start, "end": end}, summary=summary,
                         graph=graph_dto, alternatives=alternatives, segments=segments,
                         recommendation=_recommend(segments), data_notes=notes)
