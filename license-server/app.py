import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import psycopg
from fastapi import FastAPI, Header, HTTPException, Request
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field


app = FastAPI(title="PDV Intelbras License Server", version="0.1.0")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def database_url() -> str:
    url = env("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL ausente")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    return url


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def connect():
    return psycopg.connect(database_url(), row_factory=dict_row)


def require_admin(token: str | None):
    expected = env("ADMIN_TOKEN")
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN ausente")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="admin token invalido")


def license_signature(license_key: str, valid_until: datetime, device_id: str = "") -> str:
    secret = env("LICENSE_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail="LICENSE_SECRET ausente")
    payload = "%s|%s|%s" % (license_key, valid_until.isoformat(), device_id or "")
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def make_license_key() -> str:
    return "pdv_%s" % secrets.token_urlsafe(24).replace("-", "").replace("_", "")[:32]


def init_db():
    with connect() as conn:
        conn.execute(
            """
            create table if not exists customers (
                id uuid primary key,
                name text not null,
                email text,
                asaas_customer_id text,
                created_at timestamptz not null default now()
            )
            """
        )
        conn.execute(
            """
            create table if not exists pdv_licenses (
                id uuid primary key,
                customer_id uuid references customers(id),
                store_name text not null,
                pdv_number text not null,
                license_key text unique not null,
                device_id text,
                active boolean not null default true,
                valid_until timestamptz not null,
                payment_reference text,
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now()
            )
            """
        )
        conn.execute(
            """
            create table if not exists payments (
                id uuid primary key,
                asaas_payment_id text unique,
                event text not null,
                status text,
                payment_reference text,
                license_key text,
                value numeric(12,2),
                payload jsonb not null,
                created_at timestamptz not null default now()
            )
            """
        )
        conn.execute("create index if not exists idx_pdv_license_key on pdv_licenses(license_key)")
        conn.execute("create index if not exists idx_pdv_payment_reference on pdv_licenses(payment_reference)")
        conn.commit()


@app.on_event("startup")
def startup():
    init_db()


class CustomerIn(BaseModel):
    name: str
    email: str = ""
    asaas_customer_id: str = ""


class PdvIn(BaseModel):
    customer_name: str
    customer_email: str = ""
    asaas_customer_id: str = ""
    store_name: str = "Matriz"
    pdv_number: str = Field(..., examples=["001"])
    payment_reference: str = Field("", description="externalReference, subscription id ou referencia usada no Asaas")
    initial_days: int = 7


class RenewIn(BaseModel):
    license_key: str
    days: int = 30


class CheckIn(BaseModel):
    license_key: str
    device_id: str = ""


@app.get("/")
def root():
    return {"ok": True, "service": "pdv-license-server"}


@app.get("/health")
def health():
    with connect() as conn:
        conn.execute("select 1")
    return {"ok": True}


@app.post("/admin/customers")
def create_customer(customer: CustomerIn, x_admin_token: str | None = Header(None)):
    require_admin(x_admin_token)
    customer_id = str(uuid4())
    with connect() as conn:
        conn.execute(
            "insert into customers (id, name, email, asaas_customer_id) values (%s, %s, %s, %s)",
            (customer_id, customer.name, customer.email, customer.asaas_customer_id),
        )
        conn.commit()
    return {"id": customer_id, "name": customer.name}


@app.post("/admin/pdvs")
def create_pdv(data: PdvIn, x_admin_token: str | None = Header(None)):
    require_admin(x_admin_token)
    customer_id = str(uuid4())
    license_id = str(uuid4())
    license_key = make_license_key()
    valid_until = now_utc() + timedelta(days=data.initial_days)
    pdv_number = "%03d" % int(data.pdv_number)
    payment_reference = data.payment_reference or license_key
    with connect() as conn:
        conn.execute(
            "insert into customers (id, name, email, asaas_customer_id) values (%s, %s, %s, %s)",
            (customer_id, data.customer_name, data.customer_email, data.asaas_customer_id),
        )
        conn.execute(
            """
            insert into pdv_licenses
                (id, customer_id, store_name, pdv_number, license_key, valid_until, payment_reference)
            values (%s, %s, %s, %s, %s, %s, %s)
            """,
            (license_id, customer_id, data.store_name, pdv_number, license_key, valid_until, payment_reference),
        )
        conn.commit()
    return {
        "customer_id": customer_id,
        "license_id": license_id,
        "license_key": license_key,
        "payment_reference": payment_reference,
        "valid_until": valid_until.isoformat(),
    }


@app.get("/admin/pdvs")
def list_pdvs(x_admin_token: str | None = Header(None)):
    require_admin(x_admin_token)
    with connect() as conn:
        rows = conn.execute(
            """
            select l.id, c.name as customer_name, l.store_name, l.pdv_number, l.license_key,
                   l.active, l.valid_until, l.payment_reference, l.device_id
              from pdv_licenses l
              left join customers c on c.id = l.customer_id
             order by c.name, l.store_name, l.pdv_number
            """
        ).fetchall()
    return {"pdvs": rows}


def renew_license(conn, license_key: str, days: int) -> dict[str, Any]:
    row = conn.execute(
        "select * from pdv_licenses where license_key = %s",
        (license_key,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="licenca nao encontrada")
    base = row["valid_until"] if row["valid_until"] and row["valid_until"] > now_utc() else now_utc()
    valid_until = base + timedelta(days=days)
    updated = conn.execute(
        """
        update pdv_licenses
           set active = true, valid_until = %s, updated_at = now()
         where license_key = %s
     returning *
        """,
        (valid_until, license_key),
    ).fetchone()
    return updated


@app.post("/admin/renew")
def admin_renew(data: RenewIn, x_admin_token: str | None = Header(None)):
    require_admin(x_admin_token)
    with connect() as conn:
        row = renew_license(conn, data.license_key, data.days)
        conn.commit()
    return {"license_key": row["license_key"], "valid_until": row["valid_until"].isoformat()}


@app.post("/licenses/check")
def check_license(data: CheckIn):
    with connect() as conn:
        row = conn.execute(
            """
            select l.*, c.name as customer_name
              from pdv_licenses l
              left join customers c on c.id = l.customer_id
             where l.license_key = %s
            """,
            (data.license_key,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="licenca nao encontrada")
        if data.device_id and row["device_id"] and row["device_id"] != data.device_id:
            raise HTTPException(status_code=403, detail="licenca vinculada a outro equipamento")
        if data.device_id and not row["device_id"]:
            conn.execute(
                "update pdv_licenses set device_id = %s, updated_at = now() where license_key = %s",
                (data.device_id, data.license_key),
            )
            conn.commit()
            row["device_id"] = data.device_id

    active = bool(row["active"]) and row["valid_until"] > now_utc()
    return {
        "active": active,
        "license_key": row["license_key"],
        "customer": row["customer_name"],
        "store_name": row["store_name"],
        "pdv_number": row["pdv_number"],
        "valid_until": row["valid_until"].isoformat(),
        "signature": license_signature(row["license_key"], row["valid_until"], row.get("device_id") or ""),
    }


def asaas_payment_reference(payment: dict[str, Any]) -> tuple[str, str]:
    license_key = str(payment.get("externalReference") or "").strip()
    reference = license_key or str(payment.get("subscription") or payment.get("id") or "").strip()
    return reference, license_key


@app.post("/webhooks/asaas")
async def asaas_webhook(request: Request, asaas_access_token: str | None = Header(None)):
    expected = env("ASAAS_WEBHOOK_TOKEN")
    if expected and (not asaas_access_token or not hmac.compare_digest(asaas_access_token, expected)):
        raise HTTPException(status_code=401, detail="webhook token invalido")

    payload = await request.json()
    event = str(payload.get("event") or "")
    payment = payload.get("payment") or {}
    payment_id = str(payment.get("id") or "")
    status = str(payment.get("status") or "")
    value = payment.get("value") or 0
    payment_reference, license_key = asaas_payment_reference(payment)

    should_renew = event in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED") or status in ("RECEIVED", "CONFIRMED")
    renewed = []
    with connect() as conn:
        conn.execute(
            """
            insert into payments
                (id, asaas_payment_id, event, status, payment_reference, license_key, value, payload)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (asaas_payment_id) do nothing
            """,
            (str(uuid4()), payment_id or None, event, status, payment_reference, license_key, value, Jsonb(payload)),
        )
        if should_renew and payment_reference:
            rows = conn.execute(
                """
                select license_key
                  from pdv_licenses
                 where license_key = %s or payment_reference = %s
                """,
                (payment_reference, payment_reference),
            ).fetchall()
            for row in rows:
                updated = renew_license(conn, row["license_key"], 30)
                renewed.append({"license_key": updated["license_key"], "valid_until": updated["valid_until"].isoformat()})
        conn.commit()

    return {"ok": True, "event": event, "status": status, "renewed": renewed}
