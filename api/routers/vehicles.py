"""Vehicle inventory browsing + per-vehicle history (SOW §2, §4, §5)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from dealer_scraper.models import Car, CarPriceHistory, VehicleAuditLog
from ..deps import get_db
from ..schemas import AuditLogOut, PageMeta, PriceHistoryOut, VehicleOut, VehiclePage

router = APIRouter(prefix="/api/vehicles", tags=["vehicles"])


@router.get("", response_model=VehiclePage)
def list_vehicles(
    db: Session = Depends(get_db),
    dealer_id: int | None = None,
    status: str | None = Query(None, description="active | removed | sold"),
    make: str | None = None,
    model: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    q: str | None = Query(None, description="Search make/model/trim/VIN"),
    sort: str = Query("id", description="id | price | year | mileage | last_seen_at"),
    order: str = Query("desc", description="asc | desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    stmt = select(Car)
    if dealer_id is not None:
        stmt = stmt.where(Car.dealer_id == dealer_id)
    if status:
        stmt = stmt.where(Car.status == status)
    if make:
        stmt = stmt.where(Car.make.ilike(make))
    if model:
        stmt = stmt.where(Car.model.ilike(model))
    if year_min is not None:
        stmt = stmt.where(Car.year >= year_min)
    if year_max is not None:
        stmt = stmt.where(Car.year <= year_max)
    if price_min is not None:
        stmt = stmt.where(Car.price >= price_min)
    if price_max is not None:
        stmt = stmt.where(Car.price <= price_max)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            Car.make.ilike(like) | Car.model.ilike(like)
            | Car.trim.ilike(like) | Car.vin.ilike(like)
        )

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()

    sort_col = {"id": Car.id, "price": Car.price, "year": Car.year,
                "mileage": Car.mileage, "last_seen_at": Car.last_seen_at}.get(sort, Car.id)
    stmt = stmt.order_by(sort_col.desc() if order == "desc" else sort_col.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    return VehiclePage(
        meta=PageMeta(total=total, page=page, page_size=page_size),
        items=list(db.execute(stmt).scalars()),
    )


def _car_or_404(db: Session, car_id: int) -> Car:
    car = db.get(Car, car_id)
    if car is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return car


@router.get("/{car_id}", response_model=VehicleOut)
def get_vehicle(car_id: int, db: Session = Depends(get_db)):
    return _car_or_404(db, car_id)


@router.get("/{car_id}/price-history", response_model=list[PriceHistoryOut])
def price_history(car_id: int, db: Session = Depends(get_db)):
    _car_or_404(db, car_id)
    stmt = (select(CarPriceHistory)
            .where(CarPriceHistory.car_id == car_id)
            .order_by(CarPriceHistory.recorded_at))
    return list(db.execute(stmt).scalars())


@router.get("/{car_id}/audit-log", response_model=list[AuditLogOut])
def audit_log(car_id: int, db: Session = Depends(get_db)):
    """SOW §5 — full event history for one vehicle."""
    _car_or_404(db, car_id)
    stmt = (select(VehicleAuditLog)
            .where(VehicleAuditLog.car_id == car_id)
            .order_by(VehicleAuditLog.occurred_at))
    return list(db.execute(stmt).scalars())
