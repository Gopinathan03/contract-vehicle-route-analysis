from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


LegStatus = Literal["GOOD", "NEEDS ATTENTION", "POOR", "INSUFFICIENT DATA"]
RouteStatus = Literal["GOOD ROUTE", "NEEDS ATTENTION", "POOR ROUTE", "INSUFFICIENT DATA"]


class Period(BaseModel):
    start: date
    end: date


class Metrics(BaseModel):
    trip_count: int
    total_load: float | None
    average_load: float | None
    average_capacity: float | None
    load_utilization_pct: float | None
    average_travel_hours: float | None
    minimum_travel_hours: float | None
    maximum_travel_hours: float | None
    travel_time_trip_count: int
    earliest_departure: datetime | None
    latest_arrival: datetime | None
    average_contract_vehicle_price: float | None
    minimum_contract_vehicle_price: float | None
    maximum_contract_vehicle_price: float | None
    contract_price_trip_count: int


class Segment(BaseModel):
    source: str
    destination: str
    status: LegStatus
    reason: str
    metrics: Metrics


class GraphNode(BaseModel):
    id: str
    label: str


class GraphEdge(BaseModel):
    source: str
    destination: str
    status: LegStatus
    reason: str
    metrics: Metrics


class NetworkGraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class RouteOption(BaseModel):
    cities: list[str]
    status: LegStatus
    legs: list[Segment]
    average_full_route_travel_hours: float | None
    average_full_route_contract_price: float | None


class Summary(BaseModel):
    trip_count: int
    average_load: float | None
    load_utilization_pct: float | None
    average_travel_hours: float | None
    average_contract_vehicle_price: float | None


class Recommendation(BaseModel):
    status: RouteStatus
    reason: str


class RouteAnalysis(BaseModel):
    route: list[str]
    graph: NetworkGraph
    alternatives: list[RouteOption]
    period: Period
    vehicle_type: Literal["CONTRACT"] = "CONTRACT"
    summary: Summary
    segments: list[Segment]
    recommendation: Recommendation
    data_notes: list[str]
