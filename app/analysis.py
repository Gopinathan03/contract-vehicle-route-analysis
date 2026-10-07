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


def _full_route_totals(
    path: list[str],
    path_waybills: dict[str, set[str]],
    movements_by_waybill: dict[str, list[tuple[str, Any]]],
    movements: dict[str, tuple[Any, Any]],
    prices_by_tssno: dict[str, float | None],
    start: date,
    end: date,
) -> tuple[float | None, float | None]:
    if len(path) < 2:
        return None, None

    route_label = " → ".join(f"{source}-{destination}" for source, destination in zip(path, path[1:]))
    waybills = path_waybills.get(route_label, set())
    leg_count = len(path) - 1
    route_journeys: list[tuple[float, float]] = []
    for waybill in waybills:
        ordered_movements = sorted(movements_by_waybill.get(waybill, []), key=lambda item: (item[1], item[0]))
        for offset in range(max(0, len(ordered_movements) - leg_count + 1)):
            chain = ordered_movements[offset:offset + leg_count]
            if len(chain) != leg_count:
                continue
            tssnos = [tssno for tssno, _ in chain]
            events = [movements.get(tssno, (None, None)) for tssno in tssnos]
            departure, arrival = events[0][0], events[-1][1]
            if not departure or not arrival or arrival <= departure:
                continue
            if not (start <= departure.date() <= end):
                continue
            if any(not dep or not arr or arr <= dep for dep, arr in events):
                continue
            if any(events[index][0] < events[index - 1][1] for index in range(1, len(events))):
                continue
            prices = [prices_by_tssno.get(tssno) for tssno in tssnos]
            if any(price is None for price in prices):
                continue
            duration = (arrival - departure).total_seconds() / 3600
            if duration > 0:
                route_journeys.append((duration, sum(price for price in prices if price is not None)))
            break

    durations = [duration for duration, _ in route_journeys]
    costs = [cost for _, cost in route_journeys]
    return (mean(durations) if durations else None, mean(costs) if costs else None)


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
    waybill_paths_table = sql.Identifier(settings.db_schema, "network_waybill_paths")
    dispatch_movements_table = sql.Identifier(settings.db_schema, "network_dispatch_movements")
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

    paths: list[list[str]] = []
    path_waybills: dict[str, set[str]] = defaultdict(set)
    movements_by_waybill: dict[str, list[tuple[str, Any]]] = defaultdict(list)
    route_contract_prices: dict[str, float | None] = {}
    movements: dict[str, tuple[Any, Any]] = {}
    contract_waybills: set[str] = set()
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

        route_codes_graph = nx.DiGraph()
        route_codes_graph.add_nodes_from(codes)
        route_codes_graph.add_edges_from(used_route_to_edge.values())
        paths = sorted(
            nx.all_simple_paths(route_codes_graph, codes[0], codes[-1]),
            key=lambda path: (len(path), tuple(path)),
        ) if nx.has_path(route_codes_graph, codes[0], codes[-1]) else []
        path_labels = [" → ".join(f"{source}-{destination}" for source, destination in zip(path, path[1:])) for path in paths]

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

            waybill_ids_query = sql.SQL("""
                SELECT DISTINCT upper(trim(w.prefix) || trim(w.wayno::text)) AS wbid
                FROM {detail} d
                JOIN {waybill} w ON w.prefix = d.prefix AND w.wayno = d.wayno
                WHERE d.tssno = ANY(%s) AND d.prefix IS NOT NULL AND d.wayno IS NOT NULL
            """).format(detail=detail_table, waybill=waybill_table)
            cursor.execute(waybill_ids_query, (tss_numbers,))
            contract_waybills = {row["wbid"] for row in cursor.fetchall() if row["wbid"]}

        path_tssnos: set[str] = set()
        if path_labels and contract_waybills:
            path_query = sql.SQL("""
                SELECT DISTINCT p.wbid, p.route_combination
                FROM {paths} p
                WHERE p.wbid = ANY(%s) AND p.route_combination = ANY(%s)
            """).format(paths=waybill_paths_table)
            cursor.execute(path_query, (list(contract_waybills), path_labels))
            path_wbids: set[str] = set()
            for row in cursor.fetchall():
                waybill = str(row["wbid"]).strip().upper()
                path_waybills[row["route_combination"]].add(waybill)
                path_wbids.add(waybill)

            if path_wbids:
                movement_query = sql.SQL("""
                    SELECT DISTINCT upper(trim(wbid)) AS wbid,
                           upper(trim(tssno)) AS tssno, tssdate
                    FROM {dispatch_movements}
                    WHERE wbid = ANY(%s) AND tssdate >= %s::date - interval '1 day'
                    ORDER BY upper(trim(wbid)), tssdate, upper(trim(tssno))
                """).format(dispatch_movements=dispatch_movements_table)
                cursor.execute(movement_query, (list(path_wbids), start))
                for row in cursor.fetchall():
                    if row["tssno"] and row["tssdate"]:
                        movements_by_waybill[row["wbid"]].append((row["tssno"], row["tssdate"]))
                        path_tssnos.add(row["tssno"])

        event_tssnos = set(tss_numbers) | path_tssnos
        if event_tssnos:
            event_query = sql.SQL("""
                SELECT upper(trim(tssno)) AS tssno,
                       min(date + time) FILTER (WHERE lower(trim(type)) = 'despatch') AS departure,
                       max(date + time) FILTER (WHERE lower(trim(type)) = 'arrival') AS arrival
                FROM {events}
                WHERE upper(trim(tssno)) = ANY(%s) AND lower(trim(type)) IN ('despatch', 'arrival')
                GROUP BY upper(trim(tssno))
            """).format(events=events_table)
            cursor.execute(event_query, (list(event_tssnos),))
            movements = {row["tssno"]: (row["departure"], row["arrival"]) for row in cursor.fetchall()}

        if path_tssnos:
            price_query = sql.SQL("""
                SELECT upper(trim(tssno)) AS tssno, avg(tothire::double precision) AS price
                FROM {contract}
                WHERE upper(trim(tssno)) = ANY(%s)
                GROUP BY upper(trim(tssno))
            """).format(contract=contract_table)
            cursor.execute(price_query, (list(path_tssnos),))
            route_contract_prices = {
                row["tssno"]: _number(row["price"]) for row in cursor.fetchall()
            }

    rows_by_edge: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    trips_by_edge: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    edge_by_tssno: dict[str, tuple[str, str]] = {}
    prices_by_tssno_values: dict[str, set[float]] = defaultdict(set)
    for trip in trip_rows:
        edge = used_route_to_edge.get(trip["route_code"])
        if edge is None:
            continue
        trips_by_edge[edge].append(trip)
        normalized_tssno = str(trip["tssno"]).strip().upper()
        edge_by_tssno[normalized_tssno] = edge
        price = _number(trip["contract_price"])
        if price is not None:
            prices_by_tssno_values[normalized_tssno].add(price)
        candidate_capacities = capacities.get(trip["vehicle_no"] or "", [])
        valid_capacity = [item for item in candidate_capacities if item[0] is not None and item[0] <= trip["trip_date"]]
        capacity = valid_capacity[-1][1] if valid_capacity else None
        departure, arrival = movements.get(trip["tssno"].strip().upper(), (None, None))
        travel_hours = (arrival - departure).total_seconds() / 3600 if departure and arrival and arrival > departure else None
        rows_by_edge[edge].append({
            "load": loads.get(trip["tssno"]),
            "capacity": capacity,
            "price": price,
            "travel_hours": travel_hours,
            "departure": departure,
            "arrival": arrival,
        })

    prices_by_tssno = {
        tssno: mean(values) if values else None
        for tssno, values in prices_by_tssno_values.items()
    }
    prices_by_tssno.update({tssno: price for tssno, price in route_contract_prices.items() if price is not None})
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
        full_time, full_cost = _full_route_totals(
            path, path_waybills, movements_by_waybill, movements, prices_by_tssno, start, end
        )
        if len(path_legs) == 1 and full_time is None:
            # A direct leg is already the full route; retain its measured trip
            # average when no canonical WHEAD path record exists for that trip.
            full_time = path_legs[0].metrics.average_travel_hours
            full_cost = path_legs[0].metrics.average_contract_vehicle_price
        alternatives.append(RouteOption(
            cities=[city_by_code[code] for code in path],
            status=min(path_legs, key=lambda leg: {"POOR": 0, "NEEDS ATTENTION": 1, "INSUFFICIENT DATA": 2, "GOOD": 3}[leg.status]).status,
            legs=path_legs,
            average_full_route_travel_hours=full_time,
            average_full_route_contract_price=full_cost,
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
        "Route combinations include only Coimbatore-to-Chennai paths whose direct legs have contract trips in the requested analysis window. Leg movements can belong to different shipments, so route totals use matched waybill paths.",
        "Full-route time is the first dispatch to final arrival for a chronological, complete WHEAD waybill path. Full-route contract cost sums the leg hire amounts for that same path; incomplete paths or legs without contract hire are excluded.",
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
