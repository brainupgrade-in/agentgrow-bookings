"""FastAPI entry point: WhatsApp webhook (verify handshake + signed message delivery).

Run: uv run uvicorn bookings.app:default_app --factory --port 8080
"""
import dataclasses
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from bookings.api import make_api
from bookings.channels.whatsapp import Inbound, download_media, parse_messages, send_text, signature_ok

log = logging.getLogger("bookings")
TICK_SECONDS = 300  # deposits, care-plan reminders, weekly report


def create_app(handle: Callable[[Inbound], str | None], send: Callable[[str, str], None], conn=None, sim=None,
               fetch_media: Callable[[str], bytes] = download_media, tick: Callable[[], object] | None = None) -> FastAPI:
    app = FastAPI(title="AgentGrow Bookings", docs_url=None, redoc_url=None, openapi_url=None)
    if conn is not None:
        app.include_router(make_api(conn))
    if sim is not None:
        app.include_router(sim.router())

    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    @app.get("/")
    @app.get("/demo")
    def demo():
        # revalidate every load, so judges never see a stale page after a deploy
        return FileResponse(Path(__file__).parent / "static" / "demo.html", headers={"Cache-Control": "no-cache"})

    seen_ids: set[str] = set()  # Meta retries deliveries; process each message id once (Agent also dedupes in the DB)

    def process(inbound: Inbound) -> None:
        try:
            if inbound.media_id:
                inbound = dataclasses.replace(inbound, image=fetch_media(inbound.media_id))
            reply = handle(inbound)
            if reply:
                send(inbound.sender, reply)
        except Exception:
            log.exception("failed to handle %s", inbound.msg_id)

    @app.post("/webhook/cashfree")
    async def cashfree(tasks: BackgroundTasks):
        # The body is not trusted: tick() asks Cashfree's API for the status of every pending deposit link.
        if tick is not None:
            tasks.add_task(tick)
        return {"ok": True}

    @app.get("/webhook/whatsapp")
    def verify(
        mode: str = Query("", alias="hub.mode"),
        token: str = Query("", alias="hub.verify_token"),
        challenge: str = Query("", alias="hub.challenge"),
    ):
        if mode == "subscribe" and token == os.environ["WHATSAPP_VERIFY_TOKEN"]:
            return Response(challenge, media_type="text/plain")
        return Response(status_code=403)

    @app.post("/webhook/whatsapp")
    async def receive(request: Request, tasks: BackgroundTasks):
        body = await request.body()
        if not signature_ok(body, request.headers.get("X-Hub-Signature-256"), os.environ["WHATSAPP_APP_SECRET"]):
            return Response(status_code=403)
        for inbound in parse_messages(json.loads(body)):
            if inbound.msg_id not in seen_ids:
                seen_ids.add(inbound.msg_id)
                tasks.add_task(process, inbound)  # ack Meta first; a slow model call must not trigger redelivery
        return {"ok": True}

    return app


def default_app() -> FastAPI:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    from bookings import db
    from bookings.agent import Agent, nebius_client
    from bookings.seed import ensure_seeded
    from bookings.sim import Sim

    path = Path(os.environ.get("BOOKINGS_DB", "data/bookings.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(path)
    db.init(conn)
    ensure_seeded(conn)
    agent = Agent(conn, business_id=1, llm=nebius_client(), send=send_text)
    lock = threading.Lock()  # one shared SQLite connection: webhook turns and the scheduler take turns

    def handle(inbound):
        with lock:
            return agent.handle(inbound)

    def tick():
        with lock:
            try:
                return agent.tick()
            except Exception:
                log.exception("tick failed")

    def scheduler():
        while True:
            time.sleep(TICK_SECONDS)
            tick()

    threading.Thread(target=scheduler, daemon=True, name="tick").start()
    return create_app(handle=handle, send=send_text, conn=conn, sim=Sim(nebius_client()), tick=tick)
